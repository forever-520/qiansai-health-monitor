#!/usr/bin/env python3
"""
RK 侧 Python 网关：
1) 连接 RK3576 从机 TCP 协议服务（9001）
2) 收原始雷达帧、心跳、状态、图像
3) 解析雷达帧为实时状态
4) 通过 WebSocket 推给 health_monitor 前端 UI
5) 提供最小 HTTP 静态文件服务
"""

import argparse
import asyncio
import cgi
import concurrent.futures
import ipaddress
import io
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.care_logic import evaluate_care
from rk_web_ui.yolo_detector import detect_bed_state


MAGIC = b"HM"
MAX_PAYLOAD = 10 * 1024 * 1024

TYPE_HEARTBEAT = 0x01
TYPE_HEARTBEAT_ACK = 0x02
TYPE_RADAR_DATA = 0x10
TYPE_IMAGE_REQUEST = 0x20
TYPE_IMAGE_DATA = 0x21
TYPE_IMAGE_ERROR = 0x22
TYPE_STATUS_REQUEST = 0x30
TYPE_STATUS_RESPONSE = 0x31
TYPE_ERROR = 0xF0

TEXT = {
    "exist": {0: "无人", 1: "有人"},
    "motion": {0: "无人", 1: "静止", 2: "活跃"},
    "breath": {1: "正常", 2: "呼吸过高", 3: "呼吸过低", 4: "无呼吸"},
    "bed": {0: "离床", 1: "入床", 2: "无"},
    "sleep": {0: "深睡", 1: "浅睡", 2: "清醒", 3: "无人"},
}

DISCOVERY_CACHE = Path(__file__).resolve().parent / "data" / "lubancat_last_ip.txt"
DISCOVERY_HTTP_PORT = int(os.environ.get("LUBANCAT_HTTP_PORT", "8000"))
DISCOVERY_PREFERRED_HOSTS = os.environ.get("LUBANCAT_PREFERRED_HOSTS", "")
DISCOVERY_PREFERRED_TIMEOUT = float(os.environ.get("LUBANCAT_PREFERRED_TIMEOUT", "8.0"))
DISCOVERY_SCAN_TIMEOUT = float(os.environ.get("LUBANCAT_SCAN_TIMEOUT", "0.45"))
REMOTE_HTTP_TIMEOUT = float(os.environ.get("LUBANCAT_POLL_TIMEOUT", "8.0"))
REMOTE_CAMERA_TIMEOUT = float(os.environ.get("LUBANCAT_CAMERA_TIMEOUT", "35.0"))
CAMERA_JPEG_MAX_SIDE = int(os.environ.get("CAMERA_JPEG_MAX_SIDE", "960"))
CAMERA_JPEG_QUALITY = int(os.environ.get("CAMERA_JPEG_QUALITY", "75"))


def compress_camera_jpeg(jpeg_data):
    if CAMERA_JPEG_MAX_SIDE <= 0 and CAMERA_JPEG_QUALITY >= 95:
        return jpeg_data
    try:
        from PIL import Image
    except Exception:
        return jpeg_data
    try:
        image = Image.open(io.BytesIO(jpeg_data))
        image.thumbnail((CAMERA_JPEG_MAX_SIDE, CAMERA_JPEG_MAX_SIDE))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=max(30, min(95, CAMERA_JPEG_QUALITY)),
            optimize=True,
        )
        compressed = output.getvalue()
        # 压缩后反而更大时保留原图，避免弱网下增加负担。
        return compressed if len(compressed) < len(jpeg_data) else jpeg_data
    except Exception as exc:
        print(f"CAMERA_COMPRESS_SKIP {exc}", flush=True)
        return jpeg_data


def is_auto_value(value):
    return str(value or "").strip().lower() in {"auto", "discover", "scan"}


def remote_urls_for_host(host, http_port=DISCOVERY_HTTP_PORT):
    return (
        f"http://{host}:{http_port}/radar/raw",
        f"http://{host}:{http_port}/camera/capture",
    )


def _preferred_hosts(value=DISCOVERY_PREFERRED_HOSTS):
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]


def _local_ipv4_networks():
    networks = []
    try:
        output = subprocess.check_output(
            ["ip", "-o", "-4", "addr", "show", "scope", "global"],
            text=True,
            timeout=2,
        )
        for line in output.splitlines():
            parts = line.split()
            if "inet" in parts:
                networks.append(parts[parts.index("inet") + 1])
    except Exception:
        try:
            host = socket.gethostbyname(socket.gethostname())
            if not host.startswith("127."):
                networks.append(f"{host}/24")
        except OSError:
            pass
    return networks


def _candidate_hosts(networks):
    candidates = []
    seen = set()
    for item in networks:
        try:
            interface = ipaddress.ip_interface(item)
        except ValueError:
            continue
        own_ip = str(interface.ip)
        network = interface.network
        # 避免在大网段里扫太久；常见 WiFi/LAN 按 /24 扫描足够。
        if network.prefixlen < 24:
            network = ipaddress.ip_network(f"{interface.ip}/24", strict=False)
        for host in network.hosts():
            value = str(host)
            if value == own_ip or value in seen:
                continue
            seen.add(value)
            candidates.append(value)
    return candidates


def _probe_lubancat_http(host, port=DISCOVERY_HTTP_PORT, timeout=0.45):
    try:
        with urlopen(f"http://{host}:{port}/radar/raw", timeout=timeout) as response:
            snap = json.loads(response.read(4096).decode("utf-8", "replace"))
        return all(key in snap for key in ("human", "heart", "breath", "system"))
    except Exception:
        return False


def _read_cached_host(cache_path=DISCOVERY_CACHE):
    try:
        value = Path(cache_path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return value


def _write_cached_host(host, cache_path=DISCOVERY_CACHE):
    try:
        path = Path(cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{host}\n", encoding="utf-8")
    except OSError:
        pass


def discover_lubancat_http_host(
    configured_host="auto",
    http_port=DISCOVERY_HTTP_PORT,
    cache_path=DISCOVERY_CACHE,
    networks=None,
    probe=_probe_lubancat_http,
    max_workers=32,
    preferred_hosts=None,
):
    if not is_auto_value(configured_host):
        return configured_host

    for host in _preferred_hosts(DISCOVERY_PREFERRED_HOSTS if preferred_hosts is None else preferred_hosts):
        if probe(host, http_port, DISCOVERY_PREFERRED_TIMEOUT):
            _write_cached_host(host, cache_path)
            print(f"LUBANCAT_DISCOVERY preferred {host}", flush=True)
            return host

    cached = _read_cached_host(cache_path)
    if cached and probe(cached, http_port, DISCOVERY_PREFERRED_TIMEOUT):
        print(f"LUBANCAT_DISCOVERY cached {cached}", flush=True)
        return cached

    candidates = _candidate_hosts(networks or _local_ipv4_networks())
    if not candidates:
        print("LUBANCAT_DISCOVERY no local IPv4 network found", flush=True)
        return ""

    print(f"LUBANCAT_DISCOVERY scanning {len(candidates)} hosts on port {http_port}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {executor.submit(probe, candidate, http_port, DISCOVERY_SCAN_TIMEOUT): candidate for candidate in candidates}
        for future in concurrent.futures.as_completed(futures):
            candidate = futures[future]
            try:
                ok = bool(future.result())
            except Exception:
                ok = False
            if ok:
                _write_cached_host(candidate, cache_path)
                print(f"LUBANCAT_DISCOVERY found {candidate}", flush=True)
                return candidate

    print("LUBANCAT_DISCOVERY not found", flush=True)
    return ""


@dataclass
class RadarSnapshot:
    human_exist: int = 0
    motion_state: int = 0
    motion_val: int = 0
    distance_cm: int = 0
    x: int = 0
    y: int = 0
    z: int = 0
    heart_bpm: int = 0
    breath_rpm: int = 0
    breath_state: int = 0
    bed: int = 0
    sleep: int = 3
    heart_wave: List[int] = field(default_factory=list)
    breath_wave: List[int] = field(default_factory=list)
    frame_count: int = 0
    checksum_errors: int = 0
    parse_errors: int = 0
    online: bool = False
    camera_ok: bool = False
    wifi_rssi: int = 0xFF
    last_image_jpeg: bytes = b""
    last_image_ts_ms: int = 0
    last_heartbeat_ack_ms: int = 0
    last_frame_ts_ms: int = 0


class SharedState:
    def __init__(self):
        self._lock = threading.Lock()
        self.snapshot = RadarSnapshot()
        self._heart_phase = 0.0
        self._breath_phase = 0.0

    def update_from_remote_snapshot(self, snap):
        with self._lock:
            human = snap.get("human", {})
            heart = snap.get("heart", {})
            breath = snap.get("breath", {})
            sleep = snap.get("sleep", {})
            system = snap.get("system", {})
            self.snapshot.human_exist = int(human.get("exist", 0) or 0)
            self.snapshot.motion_state = int(human.get("motion_state", 0) or 0)
            self.snapshot.motion_val = int(human.get("motion_val", 0) or 0)
            self.snapshot.distance_cm = int(human.get("distance", 0) or 0)
            self.snapshot.x = int(human.get("x", 0) or 0)
            self.snapshot.y = int(human.get("y", 0) or 0)
            self.snapshot.z = int(human.get("z", 0) or 0)
            self.snapshot.heart_bpm = int(heart.get("rate", 0) or 0)
            self.snapshot.breath_rpm = int(breath.get("rate", 0) or 0)
            self.snapshot.breath_state = int(breath.get("state", 0) or 0)
            self.snapshot.bed = int(sleep.get("bed", 0) or 0)
            self.snapshot.sleep = int(sleep.get("state", 3) or 3)
            self.snapshot.heart_wave = list(heart.get("wave", []))[-512:]
            self.snapshot.breath_wave = list(breath.get("wave", []))[-512:]
            self.snapshot.frame_count = int(system.get("frame_count", 0) or 0)
            self.snapshot.checksum_errors = int(system.get("checksum_error_count", 0) or 0)
            self.snapshot.parse_errors = int(system.get("parse_error_count", 0) or 0)
            remote_ts = int(snap.get("timestamp_ms") or system.get("timestamp_ms") or 0)
            remote_frame_ts = int(system.get("last_frame_ms") or system.get("last_frame_ts_ms") or 0)
            self.snapshot.last_frame_ts_ms = remote_frame_ts or remote_ts or int(time.time() * 1000)
            self.snapshot.online = True

    def signed_16(self, high, low):
        value = (high << 8) | low
        if value & 0x8000:
            value -= 0x10000
        return value

    def checksum(self, data):
        return sum(data) & 0xFF

    def update_from_radar_frame(self, frame):
        with self._lock:
            self.snapshot.online = True
            self.snapshot.frame_count += 1
            self.snapshot.last_frame_ts_ms = int(time.time() * 1000)

            if len(frame) < 9 or frame[:2] != b"\x53\x59" or frame[-2:] != b"\x54\x43":
                self.snapshot.parse_errors += 1
                return

            ctrl = frame[2]
            cmd = frame[3]
            length = (frame[4] << 8) | frame[5]
            payload = list(frame[6:6 + length])
            recv_checksum = frame[6 + length]
            if self.checksum(frame[:6 + length]) != recv_checksum:
                self.snapshot.checksum_errors += 1
                return

            try:
                if ctrl == 0x80 and cmd in (0x01, 0x81) and payload:
                    self.snapshot.human_exist = payload[0]
                elif ctrl == 0x80 and cmd == 0x02 and payload:
                    self.snapshot.motion_state = payload[0]
                elif ctrl == 0x80 and cmd in (0x03, 0x83) and payload:
                    self.snapshot.motion_val = payload[0]
                elif ctrl == 0x80 and cmd == 0x04 and len(payload) >= 2:
                    self.snapshot.distance_cm = (payload[0] << 8) | payload[1]
                elif ctrl == 0x80 and cmd == 0x05 and len(payload) >= 6:
                    self.snapshot.x = self.signed_16(payload[0], payload[1])
                    self.snapshot.y = self.signed_16(payload[2], payload[3])
                    self.snapshot.z = self.signed_16(payload[4], payload[5])
                elif ctrl == 0x85 and cmd in (0x02, 0x82) and payload:
                    self.snapshot.heart_bpm = payload[0]
                elif ctrl == 0x85 and cmd in (0x05, 0x85) and payload:
                    self.snapshot.heart_wave.extend(v - 128 for v in payload)
                    if len(self.snapshot.heart_wave) > 512:
                        del self.snapshot.heart_wave[:-512]
                elif ctrl == 0x81 and cmd == 0x01 and payload:
                    self.snapshot.breath_state = payload[0]
                elif ctrl == 0x81 and cmd in (0x02, 0x82) and payload:
                    self.snapshot.breath_rpm = payload[0]
                elif ctrl == 0x81 and cmd in (0x05, 0x85) and payload:
                    self.snapshot.breath_wave.extend(v - 128 for v in payload)
                    if len(self.snapshot.breath_wave) > 512:
                        del self.snapshot.breath_wave[:-512]
                elif ctrl == 0x84 and cmd == 0x01 and payload:
                    self.snapshot.bed = payload[0]
                elif ctrl == 0x84 and cmd == 0x02 and payload:
                    self.snapshot.sleep = payload[0]
            except Exception:
                self.snapshot.parse_errors += 1

    def update_status(self, payload):
        if len(payload) < 7:
            return
        with self._lock:
            self.snapshot.online = bool(payload[0])
            self.snapshot.camera_ok = bool(payload[1])
            self.snapshot.wifi_rssi = payload[2]

    def update_image(self, jpeg_data):
        jpeg_data = compress_camera_jpeg(jpeg_data)
        with self._lock:
            self.snapshot.last_image_jpeg = jpeg_data
            self.snapshot.last_image_ts_ms = int(time.time() * 1000)

    def get_latest_image(self):
        with self._lock:
            return self.snapshot.last_image_jpeg, self.snapshot.last_image_ts_ms

    def mark_heartbeat_ack(self):
        with self._lock:
            self.snapshot.last_heartbeat_ack_ms = int(time.time() * 1000)

    def build_vital_message(self):
        with self._lock:
            snap = self.snapshot
            # 真实链路优先：没有真实数值时先显示 0，避免误导。
            heart_bpm = snap.heart_bpm if snap.heart_bpm > 0 else 0
            breath_rpm = snap.breath_rpm if snap.breath_rpm > 0 else 0
            motion_val = snap.motion_val if snap.motion_val > 0 else 0
            display_exist = snap.human_exist if snap.human_exist in (0, 1) else 0
            presence = TEXT["exist"].get(display_exist, "无人")
            stability = "稳定" if snap.motion_state in (0, 1) else "活跃"
            care = evaluate_care(
                exist=display_exist,
                bed=snap.bed,
                sleep_state=snap.sleep,
                motion_val=motion_val,
                heart_rate=heart_bpm,
                breath_rate=breath_rpm,
                frame_count=snap.frame_count,
                online=snap.online,
            )
            return {
                "type": "vital_signs",
                "ts": int(time.time() * 1000),
                "last_frame_ms": snap.last_frame_ts_ms,
                "data": {
                    "hr": float(heart_bpm),
                    "br": float(breath_rpm),
                    "motion": float(motion_val),
                    "presence": presence,
                    "stability": stability,
                    "bedState": TEXT["bed"].get(snap.bed, "未知"),
                    "heartValid": heart_bpm > 0,
                    "breathValid": breath_rpm > 0,
                    "online": snap.online,
                    "ts": int(time.time() * 1000),
                    "lastFrameMs": snap.last_frame_ts_ms,
                    "care": care.to_dict(),
                },
            }

    def _fallback_wave(self, kind, rate, source_wave):
        if len(source_wave) >= 24 and len(set(source_wave)) > 2:
            wave = list(source_wave[-512:])
            # UI 需要 0~255，中线 128。
            return [max(0, min(255, v + 128)) for v in wave]

        if kind == "heart":
            self._heart_phase += 9.0
            safe_rate = rate if rate >= 40 else 72
            beat_period = max(36.0, 4800.0 / max(safe_rate, 1))
            values = []
            for i in range(512):
                t = ((i + self._heart_phase) % beat_period) / beat_period
                signal = (
                    18 * math.exp(-((t - 0.16) / 0.04) ** 2)
                    - 24 * math.exp(-((t - 0.31) / 0.014) ** 2)
                    + 120 * math.exp(-((t - 0.335) / 0.010) ** 2)
                    - 36 * math.exp(-((t - 0.365) / 0.018) ** 2)
                    + 22 * math.exp(-((t - 0.58) / 0.08) ** 2)
                )
                baseline = 3.5 * math.sin((i + self._heart_phase) / 37.0) + 1.8 * math.sin((i + self._heart_phase) / 13.0)
                values.append(max(0, min(255, int(signal + baseline + 128))))
            return values

        self._breath_phase += 4.5
        safe_rate = rate if rate >= 6 else 16
        breath_period = max(90.0, 3600.0 / max(safe_rate, 1))
        return [
            max(0, min(255, int(
                34 * math.sin((i + self._breath_phase) * (2 * math.pi / breath_period))
                + 8 * math.sin((i + self._breath_phase) * (4 * math.pi / breath_period) - 0.7)
                + 3 * math.sin((i + self._breath_phase) / 41.0)
                + 128
            )))
            for i in range(512)
        ]

    def build_waveform_message(self):
        with self._lock:
            snap = self.snapshot
            heart = self._fallback_wave("heart", snap.heart_bpm, snap.heart_wave)
            breath = self._fallback_wave("breath", snap.breath_rpm, snap.breath_wave)
            return {
                "type": "waveform",
                "ts": int(time.time() * 1000),
                "last_frame_ms": snap.last_frame_ts_ms,
                "heart": heart,
                "breath": breath,
            }

    def build_stats_message(self):
        with self._lock:
            snap = self.snapshot
            return {
                "type": "stats",
                "ts": int(time.time() * 1000),
                "last_frame_ms": snap.last_frame_ts_ms,
                "frame_count": snap.frame_count,
                "parser_err": snap.parse_errors,
                "crc_err": snap.checksum_errors,
                "online": snap.online,
            }

    def build_raw_snapshot(self):
        with self._lock:
            snap = self.snapshot
            care = evaluate_care(
                exist=snap.human_exist,
                bed=snap.bed,
                sleep_state=snap.sleep,
                motion_val=snap.motion_val,
                heart_rate=snap.heart_bpm,
                breath_rate=snap.breath_rpm,
                frame_count=snap.frame_count,
                online=snap.online,
            )
            return {
                "human": {
                    "exist": snap.human_exist,
                    "motion_state": snap.motion_state,
                    "motion_val": snap.motion_val,
                    "distance": snap.distance_cm,
                    "x": snap.x,
                    "y": snap.y,
                    "z": snap.z,
                },
                "heart": {
                    "rate": snap.heart_bpm,
                    "wave": list(snap.heart_wave[-512:]),
                },
                "breath": {
                    "rate": snap.breath_rpm,
                    "state": snap.breath_state,
                    "wave": list(snap.breath_wave[-512:]),
                },
                "sleep": {
                    "bed": snap.bed,
                    "state": snap.sleep,
                    "awake_time": 0,
                    "light_time": 0,
                    "deep_time": 0,
                    "score": 0,
                    "avg_breath": 0,
                    "avg_heart": 0,
                    "turn_over": 0,
                    "big_motion_ratio": 0,
                    "small_motion_ratio": 0,
                    "apnea": 0,
                    "total_sleep": 0,
                    "awake_ratio": 0,
                    "light_ratio": 0,
                    "deep_ratio": 0,
                    "out_bed_time": 0,
                    "out_bed_count": 0,
                    "exception": 3,
                    "rating": 0,
                    "struggle": 0,
                    "nobody_timer": 0,
                },
                "system": {
                    "timestamp_ms": int(time.time() * 1000),
                    "frame_count": snap.frame_count,
                    "parse_error_count": snap.parse_errors,
                    "checksum_error_count": snap.checksum_errors,
                    "last_frame_hex": "",
                    "last_frame_ms": snap.last_frame_ts_ms,
                    "online": snap.online,
                    "camera_ok": snap.camera_ok,
                    "wifi_rssi": snap.wifi_rssi,
                    "last_image_ts_ms": snap.last_image_ts_ms,
                },
                "care": care.to_dict(),
            }

    def build_alarms_message(self):
        with self._lock:
            snap = self.snapshot
            now_s = time.strftime("%H:%M:%S")
            alarms = []
            if snap.heart_bpm > 100 or (snap.heart_bpm > 0 and snap.heart_bpm < 50):
                alarms.append({"level": "error", "title": "心率超限", "time": now_s, "detail": f"{snap.heart_bpm} bpm，超出配置范围"})
            elif snap.heart_bpm > 90 or (snap.heart_bpm > 0 and snap.heart_bpm < 55):
                alarms.append({"level": "warn", "title": "心率边缘", "time": now_s, "detail": f"{snap.heart_bpm} bpm，趋近阈值"})
            if snap.breath_rpm > 24 or (snap.breath_rpm > 0 and snap.breath_rpm < 8):
                alarms.append({"level": "error", "title": "呼吸率超限", "time": now_s, "detail": f"{snap.breath_rpm} rpm，超出配置范围"})
            if snap.motion_val < 5:
                alarms.append({"level": "warn", "title": "长时间低体动", "time": now_s, "detail": "体动强度连续低于阈值，等待二次确认"})
            if not alarms:
                alarms.append({"level": "normal", "title": "系统正常", "time": now_s, "detail": "所有指标在配置范围内"})
            return {"type": "alarms", "ts": int(time.time() * 1000), "alarms": alarms}


class ProtocolClient:
    def __init__(self, host, port, state):
        self.host = host
        self.port = port
        self.state = state
        self.sock = None
        self.seq = 1
        self.send_lock = threading.Lock()
        self.stop_event = threading.Event()

    def crc16_modbus(self, data):
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc & 0xFFFF

    def pack_frame(self, msg_type, seq, payload=b""):
        header = MAGIC + bytes([msg_type]) + len(payload).to_bytes(4, "big") + seq.to_bytes(4, "big")
        body = header + payload
        return body + self.crc16_modbus(body).to_bytes(2, "big")

    def recv_exact(self, size):
        chunks = []
        remaining = size
        while remaining > 0:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise ConnectionError("socket closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def read_frame(self):
        header = self.recv_exact(11)
        if header[:2] != MAGIC:
            raise ConnectionError("bad magic")
        msg_type = header[2]
        payload_len = int.from_bytes(header[3:7], "big")
        seq = int.from_bytes(header[7:11], "big")
        if payload_len > MAX_PAYLOAD:
            raise ConnectionError(f"payload too large: {payload_len}")
        payload = self.recv_exact(payload_len)
        recv_crc = int.from_bytes(self.recv_exact(2), "big")
        calc_crc = self.crc16_modbus(header + payload)
        if recv_crc != calc_crc:
            raise ConnectionError("crc mismatch")
        return msg_type, seq, payload

    def next_seq(self):
        seq = self.seq
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        return seq

    def send_frame(self, msg_type, payload=b""):
        seq = self.next_seq()
        frame = self.pack_frame(msg_type, seq, payload)
        with self.send_lock:
            self.sock.sendall(frame)
        return seq

    def request_image(self, trigger=0x01, count=1):
        self.send_frame(TYPE_IMAGE_REQUEST, bytes([trigger, count]))

    def request_status(self):
        self.send_frame(TYPE_STATUS_REQUEST, b"")

    def heartbeat_loop(self):
        while not self.stop_event.is_set():
            try:
                self.send_frame(TYPE_HEARTBEAT, int(time.time() * 1000).to_bytes(8, "big"))
            except OSError:
                return
            time.sleep(5)

    def status_loop(self):
        while not self.stop_event.is_set():
            try:
                self.request_status()
            except OSError:
                return
            time.sleep(30)

    def recv_loop(self):
        while not self.stop_event.is_set():
            msg_type, _, payload = self.read_frame()
            if msg_type == TYPE_HEARTBEAT_ACK:
                self.state.mark_heartbeat_ack()
            elif msg_type == TYPE_STATUS_RESPONSE:
                self.state.update_status(payload)
            elif msg_type == TYPE_RADAR_DATA:
                frame_len = int.from_bytes(payload[:2], "big")
                frame = payload[2:2 + frame_len]
                self.state.update_from_radar_frame(frame)
            elif msg_type == TYPE_IMAGE_DATA:
                jpeg_len = int.from_bytes(payload[1:5], "big")
                jpeg_data = payload[5:5 + jpeg_len]
                self.state.update_image(jpeg_data)
            elif msg_type == TYPE_IMAGE_ERROR:
                print(f"IMAGE_ERROR code={payload[0] if payload else 'unknown'}", flush=True)
            elif msg_type == TYPE_ERROR:
                code = payload[0] if payload else 0xFF
                text = payload[2:].decode("utf-8", "replace") if len(payload) >= 2 else ""
                print(f"REMOTE_ERROR code={code} text={text}", flush=True)

    def run_forever(self):
        while not self.stop_event.is_set():
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=10)
                self.sock.settimeout(30)
                print(f"TCP_CONNECTED {self.host}:{self.port}", flush=True)
                hb_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
                st_thread = threading.Thread(target=self.status_loop, daemon=True)
                hb_thread.start()
                st_thread.start()
                self.recv_loop()
            except Exception as exc:
                print(f"TCP_ERROR {exc}", flush=True)
                time.sleep(2)
            finally:
                if self.sock is not None:
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                    self.sock = None


class RemoteHttpClient:
    def __init__(self, url, state, interval=1.0, capture_url="", auto_discover=False):
        self.url = url
        self.state = state
        self.interval = interval
        self.capture_url = capture_url
        self.auto_discover = auto_discover
        self.failures = 0
        self.camera_lock = threading.Lock()

    def _rediscover(self):
        if not self.auto_discover:
            return False
        host = discover_lubancat_http_host("auto")
        if not host:
            return False
        self.url, self.capture_url = remote_urls_for_host(host)
        self.failures = 0
        print(f"HTTP_DISCOVERY_ACTIVE {host}", flush=True)
        return True

    def run_forever(self):
        while True:
            try:
                if not self.url and not self._rediscover():
                    raise RuntimeError("lubancat not discovered")
                with urlopen(self.url, timeout=REMOTE_HTTP_TIMEOUT) as response:
                    snap = json.loads(response.read().decode("utf-8"))
                self.state.update_from_remote_snapshot(snap)
                self.failures = 0
                print(f"HTTP_POLL_OK {self.url}", flush=True)
            except Exception as exc:
                self.failures += 1
                print(f"HTTP_POLL_ERROR {exc}", flush=True)
                with self.state._lock:
                    self.state.snapshot.online = False
                if self.failures >= 5:
                    self._rediscover()
            time.sleep(self.interval)

    def request_image(self, trigger=0x01, count=1):
        if not self.capture_url:
            raise RuntimeError("remote camera capture url is not configured")
        # HTTP 模式下由飞凌 RK3588 转发一次拍照请求到 RK3576 从机，拿到 JPEG 后缓存给 Web/Qt 使用。
        request = Request(self.capture_url, method="POST")
        with self.camera_lock:
            with urlopen(request, timeout=REMOTE_CAMERA_TIMEOUT) as response:
                jpeg_data = response.read()
        if not jpeg_data.startswith(b"\xff\xd8"):
            raise RuntimeError("remote camera response is not jpeg")
        self.state.update_image(jpeg_data)
        with self.state._lock:
            self.state.snapshot.camera_ok = True
        return jpeg_data


class VitalBridgeServer:
    def __init__(self, state, client=None, host="0.0.0.0", port=8080, rate=20.0):
        self.host = host
        self.port = port
        self.rate = rate
        self.state = state
        self.client = client
        self.clients = set()
        self._alarm_cycle = 0

    async def handler(self, ws):
        self.clients.add(ws)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    cmd = msg.get("cmd", "")
                    if cmd == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                    elif cmd in ("capture", "camera_capture"):
                        if self.client is None:
                            await ws.send(json.dumps({"type": "camera_error", "detail": "camera client unavailable"}))
                        else:
                            self.client.request_image(trigger=0x01, count=1)
                            await ws.send(json.dumps({"type": "camera_status", "detail": "capture_requested"}))
                except Exception:
                    await ws.send(json.dumps({"type": "error", "detail": "无效消息格式"}, ensure_ascii=False))
        finally:
            self.clients.discard(ws)

    async def broadcast(self, msg):
        if not self.clients:
            return
        payload = json.dumps(msg, ensure_ascii=False)
        await asyncio.gather(*(c.send(payload) for c in self.clients.copy()), return_exceptions=True)

    async def tick_loop(self):
        interval = 1.0 / self.rate
        while True:
            t0 = time.monotonic()
            await self.broadcast(self.state.build_vital_message())
            await self.broadcast(self.state.build_waveform_message())
            await self.broadcast(self.state.build_stats_message())
            self._alarm_cycle += 1
            if self._alarm_cycle % max(1, int(self.rate * 5)) == 0:
                await self.broadcast(self.state.build_alarms_message())
            elapsed = time.monotonic() - t0
            sleep = interval - elapsed
            if sleep > 0:
                await asyncio.sleep(sleep)

    async def start(self):
        import websockets
        async with websockets.serve(self.handler, self.host, self.port, ping_interval=None):
            print(f"WebSocket Bridge → ws://{self.host}:{self.port}", flush=True)
            await self.tick_loop()


class QuietHandler(SimpleHTTPRequestHandler):
    state = None
    client = None
    capture_timeout = 8.0
    capture_refreshing = False
    capture_refresh_lock = threading.Lock()

    def log_message(self, fmt, *args):
        return

    def _send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            self._send_json({"ok": True, "service": "lubancat_rk_gateway", "websocket": "/ws"})
            return
        if self.path == "/radar/raw":
            self._send_json(self.state.build_raw_snapshot())
            return
        if self.path == "/yolo/status":
            self._send_json(
                {
                    "ok": True,
                    "enabled": False,
                    "bedOccupied": None,
                    "confidence": 0,
                    "source": "reserved",
                    "message": "YOLO接口待接入",
                    "ts": int(time.time() * 1000),
                }
            )
            return
        if self.path == "/camera/latest.jpg":
            image, _ = self.state.get_latest_image()
            if not image:
                self._send_json({"ok": False, "error": "no_image"}, status=404)
                return
            self._send_image(image)
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/yolo/detect":
            self._handle_yolo_detect()
            return
        if self.path != "/camera/capture":
            self.send_error(404, "File not found")
            return
        before_ts = self.state.get_latest_image()[1]
        if self.client is None:
            image, _ = self.state.get_latest_image()
            if image:
                self._send_image(image)
                return
            self._send_json({"ok": False, "error": "camera_client_unavailable"}, status=503)
            return
        image, _ = self.state.get_latest_image()
        if image:
            # 有缓存时立即返回给 Web，同时后台刷新下一张，避免页面等 RK3576 slave 实时拍照导致 502。
            self._refresh_camera_async()
            self._send_image(image)
            return
        try:
            self.client.request_image(trigger=0x01, count=1)
        except Exception as exc:
            self._send_json({"ok": False, "error": f"request_failed:{exc}"}, status=502)
            return

        deadline = time.monotonic() + self.capture_timeout
        while time.monotonic() < deadline:
            image, ts_ms = self.state.get_latest_image()
            if image and ts_ms != before_ts:
                self._send_image(image)
                return
            time.sleep(0.1)
        self._send_json({"ok": False, "error": "capture_timeout"}, status=504)

    def _refresh_camera_async(self):
        cls = type(self)
        with cls.capture_refresh_lock:
            if cls.capture_refreshing:
                return
            cls.capture_refreshing = True
        threading.Thread(target=self._refresh_camera_worker, daemon=True).start()

    def _refresh_camera_worker(self):
        try:
            self.client.request_image(trigger=0x01, count=1)
        except Exception as exc:
            print(f"CAMERA_REFRESH_ERROR {exc}", flush=True)
        finally:
            cls = type(self)
            with cls.capture_refresh_lock:
                cls.capture_refreshing = False

    def _handle_yolo_detect(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length)
        image_bytes, file_name, conf, iou = self._parse_yolo_request(body)
        result = detect_bed_state(image_bytes=image_bytes, conf=conf, iou=iou, file_name=file_name)
        self._send_json(result, status=200 if result.get("ok") else 503)

    def _parse_yolo_request(self, body):
        content_type = self.headers.get("Content-Type", "")
        conf = 0.25
        iou = 0.70
        file_name = "upload.jpg"
        image_bytes = b""
        if content_type.startswith("multipart/form-data"):
            environ = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(len(body)),
            }
            form = cgi.FieldStorage(fp=io.BytesIO(body), headers=self.headers, environ=environ, keep_blank_values=True)
            image_field = form["image"] if "image" in form else None
            if image_field is not None:
                file_name = Path(getattr(image_field, "filename", "") or file_name).name
                image_bytes = image_field.file.read()
            if "conf" in form:
                conf = self._to_float(form.getfirst("conf"), 0.25)
            if "iou" in form:
                iou = self._to_float(form.getfirst("iou"), 0.70)
        else:
            image_bytes = body
        return image_bytes, file_name, max(0.01, min(1.0, conf)), max(0.01, min(1.0, iou))

    @staticmethod
    def _to_float(value, fallback):
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _send_image(self, image):
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(image)))
        self.end_headers()
        self.wfile.write(image)


def start_http_server(ui_dir, port, state, client):
    os.chdir(ui_dir)
    QuietHandler.state = state
    QuietHandler.client = client
    server = ThreadingHTTPServer(("0.0.0.0", port), QuietHandler)
    print(f"HTTP Static Files → http://0.0.0.0:{port}", flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="RK-side RK3576 slave TCP gateway for health_monitor UI")
    parser.add_argument("--remote-url", default=os.environ.get("REMOTE_RADAR_URL", ""), help="Optional RK3576 slave HTTP raw snapshot URL, e.g. http://<SLAVE_IP>:8000/radar/raw")
    parser.add_argument("--remote-camera-url", default=os.environ.get("REMOTE_CAMERA_CAPTURE_URL", ""), help="Optional RK3576 slave HTTP camera capture URL")
    parser.add_argument("--lubancat-host", default=os.environ.get("LUBANCAT_HOST", "rk3576-slave.local"))
    parser.add_argument("--lubancat-port", type=int, default=int(os.environ.get("LUBANCAT_PORT", "9001")))
    parser.add_argument("--ws-port", type=int, default=int(os.environ.get("GATEWAY_WS_PORT", "8001")))
    parser.add_argument("--http-port", type=int, default=int(os.environ.get("GATEWAY_HTTP_PORT", "8000")))
    parser.add_argument("--rate", type=float, default=float(os.environ.get("GATEWAY_RATE", "20.0")), help="UI broadcast rate")
    parser.add_argument("--ui-dir", default=str(Path(__file__).resolve().parent.parent / "src" / "ui"))
    args = parser.parse_args()

    state = SharedState()
    client = None
    auto_http = is_auto_value(args.remote_url) or is_auto_value(args.lubancat_host)
    if auto_http:
        host = discover_lubancat_http_host("auto")
        remote_url, remote_camera_url = remote_urls_for_host(host) if host else ("", "")
        client = RemoteHttpClient(remote_url, state, interval=0.25, capture_url=remote_camera_url, auto_discover=True)
        threading.Thread(target=client.run_forever, daemon=True).start()
    elif args.remote_url:
        client = RemoteHttpClient(args.remote_url, state, interval=0.25, capture_url=args.remote_camera_url)
        threading.Thread(target=client.run_forever, daemon=True).start()
    else:
        client = ProtocolClient(args.lubancat_host, args.lubancat_port, state)
        threading.Thread(target=client.run_forever, daemon=True).start()
    threading.Thread(target=start_http_server, args=(args.ui_dir, args.http_port, state, client), daemon=True).start()

    server = VitalBridgeServer(state=state, client=client, host="0.0.0.0", port=args.ws_port, rate=args.rate)
    asyncio.run(server.start())


if __name__ == "__main__":
    main()
