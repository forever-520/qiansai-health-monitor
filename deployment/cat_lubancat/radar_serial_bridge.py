import argparse
import copy
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import serial


TEXT = {
    "exist": {0: "no_person", 1: "person"},
    "motion": {0: "none", 1: "still", 2: "active"},
    "breath": {1: "normal", 2: "high", 3: "low", 4: "none"},
    "bed": {0: "out_bed", 1: "in_bed", 2: "none"},
    "sleep": {0: "deep", 1: "light", 2: "awake", 3: "no_person"},
}

INIT_COMMANDS = [
    (0x80, 0x00, [0x01]),
    (0x85, 0x00, [0x01]),
    (0x81, 0x00, [0x01]),
    (0x81, 0x0B, [10]),
    (0x84, 0x00, [0x01]),
    (0x84, 0x0F, [0x00]),
]

V4L2_CTL = "/usr/bin/v4l2-ctl"
FFMPEG = "/usr/bin/ffmpeg"
RM = "/bin/rm"


def signed_16(high, low):
    value = (high << 8) | low
    if value & 0x8000:
        value -= 0x10000
    return value


def checksum(data):
    return sum(data) & 0xFF


def build_command(ctrl, cmd, payload):
    frame = [0x53, 0x59, ctrl, cmd, 0x00, len(payload)] + list(payload)
    frame.append(checksum(frame))
    frame.extend([0x54, 0x43])
    return bytes(frame)


def send_command(ser, ctrl, cmd, payload):
    ser.write(build_command(ctrl, cmd, payload))
    ser.flush()


def capture_jpeg_bytes(device="/dev/video11", width=1280, height=720, jpeg_quality=75):
    suffix = f"{threading.get_ident()}_{time.monotonic_ns()}"
    raw_path = f"/tmp/radar_capture_{suffix}.nv12"
    jpg_path = f"/tmp/radar_capture_{suffix}.jpg"

    try:
        subprocess.run(
            [V4L2_CTL, "-d", device, f"--set-fmt-video=width={width},height={height},pixelformat=NV12"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        result = subprocess.run(
            [V4L2_CTL, "-d", device, "--stream-mmap", "--stream-count", "1", "--stream-to", raw_path],
            capture_output=True,
            timeout=45,
            check=False,
        )
        if result.returncode != 0 or not Path(raw_path).exists():
            raise RuntimeError(f"v4l2 capture failed: {result.stderr.decode(errors='replace')[:200]}")

        result = subprocess.run(
            [
                FFMPEG,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "nv12",
                "-s",
                f"{width}x{height}",
                "-i",
                raw_path,
                "-frames:v",
                "1",
                "-q:v",
                str(max(1, min(31, int((100 - jpeg_quality) / 100 * 31 + 1)))),
                "-f",
                "image2",
                jpg_path,
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0 or not Path(jpg_path).exists():
            raise RuntimeError(f"ffmpeg convert failed: {result.stderr.decode(errors='replace')[:200]}")

        return Path(jpg_path).read_bytes()
    finally:
        for path in (raw_path, jpg_path):
            if not Path(path).exists():
                continue
            try:
                subprocess.run([RM, "-f", path], capture_output=True, timeout=5, check=False)
            except Exception:
                pass


class CameraCache:
    def __init__(self, device="/dev/video11", width=1280, height=720, jpeg_quality=75, interval=8.0):
        self.device = device
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality
        self.interval = interval
        self._lock = threading.Lock()
        self._capture_lock = threading.Lock()
        self.jpeg_data = b""
        self.ts_ms = 0
        self.last_error = ""

    def latest(self):
        with self._lock:
            return self.jpeg_data, self.ts_ms, self.last_error

    def capture_once(self):
        # 摄像头采集和 JPEG 编码只允许一个线程做，避免连续请求把 RK3576 slave 压住。
        with self._capture_lock:
            jpeg_data = capture_jpeg_bytes(
                device=self.device,
                width=self.width,
                height=self.height,
                jpeg_quality=self.jpeg_quality,
            )
            with self._lock:
                self.jpeg_data = jpeg_data
                self.ts_ms = int(time.time() * 1000)
                self.last_error = ""
            print(f"CAMERA_CACHE_OK size={len(jpeg_data)} ts={self.ts_ms}", flush=True)
            return jpeg_data

    def run_forever(self, stop_event):
        while not stop_event.is_set():
            try:
                self.capture_once()
            except Exception as exc:
                with self._lock:
                    self.last_error = str(exc)
                print(f"CAMERA_CACHE_ERROR {exc}", flush=True)
            stop_event.wait(max(1.0, self.interval))


class RadarState:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "human": {
                "exist": 0,
                "motion_state": 0,
                "motion_val": 0,
                "distance": 0,
                "x": 0,
                "y": 0,
                "z": 0,
            },
            "heart": {"rate": 0, "wave": []},
            "breath": {"rate": 0, "state": 0, "wave": []},
            "sleep": {"bed": 0, "state": 3},
            "system": {
                "last_frame_ts": 0,
                "last_frame_hex": "",
                "frame_count": 0,
                "checksum_error_count": 0,
                "parse_error_count": 0,
            },
        }

    def update(self, section, key, value):
        with self._lock:
            self._data[section][key] = value

    def append_wave(self, section, values, max_len=300):
        with self._lock:
            wave = self._data[section]["wave"]
            wave.extend(v - 128 for v in values)
            if len(wave) > max_len:
                del wave[:-max_len]

    def mark_frame(self, raw):
        with self._lock:
            self._data["system"]["last_frame_ts"] = time.time()
            self._data["system"]["last_frame_hex"] = raw.hex(" ").upper()
            self._data["system"]["frame_count"] += 1

    def count(self, key):
        with self._lock:
            self._data["system"][key] += 1

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._data)


def parse_frame(ctrl, cmd, payload, state):
    try:
        if ctrl == 0x80 and cmd == 0x01 and payload:
            state.update("human", "exist", payload[0])
        elif ctrl == 0x80 and cmd == 0x02 and payload:
            state.update("human", "motion_state", payload[0])
        elif ctrl == 0x80 and cmd == 0x03 and payload:
            state.update("human", "motion_val", payload[0])
        elif ctrl == 0x80 and cmd == 0x04 and len(payload) >= 2:
            state.update("human", "distance", (payload[0] << 8) | payload[1])
        elif ctrl == 0x80 and cmd == 0x05 and len(payload) >= 6:
            state.update("human", "x", signed_16(payload[0], payload[1]))
            state.update("human", "y", signed_16(payload[2], payload[3]))
            state.update("human", "z", signed_16(payload[4], payload[5]))
        elif ctrl == 0x85 and cmd == 0x02 and payload:
            state.update("heart", "rate", payload[0])
        elif ctrl == 0x85 and cmd == 0x85 and payload:
            state.append_wave("heart", payload)
        elif ctrl == 0x81 and cmd == 0x01 and payload:
            state.update("breath", "state", payload[0])
        elif ctrl == 0x81 and cmd == 0x02 and payload:
            state.update("breath", "rate", payload[0])
        elif ctrl == 0x81 and cmd == 0x85 and payload:
            state.append_wave("breath", payload)
        elif ctrl == 0x84 and cmd == 0x01 and payload:
            state.update("sleep", "bed", payload[0])
        elif ctrl == 0x84 and cmd == 0x02 and payload:
            state.update("sleep", "state", payload[0])
    except Exception:
        state.count("parse_error_count")


def parse_stream(data, state):
    index = 0
    while index < len(data) - 8:
        if data[index] != 0x53 or data[index + 1] != 0x59:
            index += 1
            continue

        ctrl = data[index + 2]
        cmd = data[index + 3]
        length = (data[index + 4] << 8) | data[index + 5]
        start = index + 6
        end = start + length
        if end + 3 > len(data):
            break

        recv_checksum = data[end]
        tail = data[end + 1 : end + 3]
        if tail != b"\x54\x43":
            index += 1
            continue

        if checksum(data[index:end]) != recv_checksum:
            state.count("checksum_error_count")
            index += 1
            continue

        raw = data[index : end + 3]
        state.mark_frame(raw)
        parse_frame(ctrl, cmd, list(data[start:end]), state)
        index = end + 3


def serial_loop(port, baud, state, stop_event):
    while not stop_event.is_set():
        try:
            with serial.Serial(port, baud, timeout=0.2) as ser:
                print(f"SERIAL_OPEN {port} {baud}", flush=True)
                write_lock = threading.Lock()

                # 初始化模块，确保数值/波形/睡眠都进入实时上报模式。
                for ctrl, cmd, payload in INIT_COMMANDS:
                    with write_lock:
                        send_command(ser, ctrl, cmd, payload)
                    time.sleep(0.3)

                threading.Thread(target=value_query_loop, args=(ser, write_lock, stop_event, 0x80), daemon=True).start()
                threading.Thread(target=value_query_loop, args=(ser, write_lock, stop_event, 0x85), daemon=True).start()
                threading.Thread(target=value_query_loop, args=(ser, write_lock, stop_event, 0x81), daemon=True).start()
                threading.Thread(target=wave_query_loop, args=(ser, write_lock, stop_event, 0x85), daemon=True).start()
                threading.Thread(target=wave_query_loop, args=(ser, write_lock, stop_event, 0x81), daemon=True).start()
                while not stop_event.is_set():
                    chunk = ser.read(256)
                    if chunk:
                        parse_stream(chunk, state)
                    time.sleep(0.02)
        except Exception as exc:
            state.count("parse_error_count")
            print(f"SERIAL_ERROR {exc}", flush=True)
            time.sleep(2)


def value_query_loop(ser, write_lock, stop_event, ctrl):
    cmd_map = {
        0x80: 0x83,  # 体动幅度查询
        0x85: 0x82,  # 心率数值查询
        0x81: 0x82,  # 呼吸数值查询
    }
    cmd = build_command(ctrl, cmd_map[ctrl], [0x0F])
    while not stop_event.is_set():
        try:
            with write_lock:
                ser.write(cmd)
                ser.flush()
        except Exception:
            return
        time.sleep(1)


def wave_query_loop(ser, write_lock, stop_event, ctrl):
    cmd = build_command(ctrl, 0x85, [0x0F])
    while not stop_event.is_set():
        try:
            with write_lock:
                ser.write(cmd)
                ser.flush()
        except Exception:
            return
        time.sleep(1)


def display_snapshot(snapshot):
    human = snapshot["human"]
    heart = snapshot["heart"]
    breath = snapshot["breath"]
    sleep = snapshot["sleep"]
    system = snapshot["system"]
    return {
        "human_exist": TEXT["exist"].get(human["exist"], "unknown"),
        "motion": TEXT["motion"].get(human["motion_state"], "unknown"),
        "distance_cm": human["distance"],
        "position_cm": {"x": human["x"], "y": human["y"], "z": human["z"]},
        "heart_bpm": heart["rate"],
        "breath_rpm": breath["rate"],
        "breath_state": TEXT["breath"].get(breath["state"], "unknown"),
        "bed": TEXT["bed"].get(sleep["bed"], "unknown"),
        "sleep": TEXT["sleep"].get(sleep["state"], "unknown"),
        "frames": system["frame_count"],
        "checksum_errors": system["checksum_error_count"],
        "parse_errors": system["parse_error_count"],
        "last_frame_hex": system["last_frame_hex"],
    }


def make_handler(state, camera_cache):
    class Handler(BaseHTTPRequestHandler):
        def _send_jpeg(self, jpeg_data, cached):
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Camera-Cached", "1" if cached else "0")
            self.send_header("Content-Length", str(len(jpeg_data)))
            self.end_headers()
            self.wfile.write(jpeg_data)

        def do_GET(self):
            if self.path == "/camera/latest.jpg":
                jpeg_data, _, last_error = camera_cache.latest()
                if jpeg_data:
                    self._send_jpeg(jpeg_data, cached=True)
                    return
                body = json.dumps({"ok": False, "error": last_error or "no_cached_image"}, ensure_ascii=False).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            snapshot = state.snapshot()
            if self.path in ("/radar", "/radar/display"):
                payload = display_snapshot(snapshot)
            elif self.path in ("/radar/raw", "/"):
                payload = snapshot
            elif self.path == "/health":
                payload = {"ok": True, "frames": snapshot["system"]["frame_count"]}
            else:
                self.send_error(404)
                return

            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/camera/capture":
                self.send_error(404)
                return
            jpeg_data, _, _ = camera_cache.latest()
            if jpeg_data:
                # RK 请求时直接返回 RK3576 slave 最近缓存图，避免每次都等待实时拍照。
                self._send_jpeg(jpeg_data, cached=True)
                return
            try:
                jpeg_data = camera_cache.capture_once()
            except Exception as exc:
                body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self._send_jpeg(jpeg_data, cached=False)

        def log_message(self, fmt, *args):
            return

    return Handler


def run_http(host, port, state, camera_cache):
    server = ThreadingHTTPServer((host, port), make_handler(state, camera_cache))
    print(f"HTTP_LISTEN http://{host}:{port}/radar", flush=True)
    server.serve_forever()


def build_parser():
    parser = argparse.ArgumentParser(description="Read R60ABD1 UART data and expose JSON over HTTP.")
    parser.add_argument("--serial-port", default="/dev/ttyS10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--camera-device", default="/dev/video11")
    parser.add_argument("--camera-width", type=int, default=1280)
    parser.add_argument("--camera-height", type=int, default=720)
    parser.add_argument("--jpeg-quality", type=int, default=75)
    parser.add_argument("--camera-cache-interval", type=float, default=8.0)
    return parser


def main():
    args = build_parser().parse_args()
    state = RadarState()
    camera_cache = CameraCache(
        device=args.camera_device,
        width=args.camera_width,
        height=args.camera_height,
        jpeg_quality=args.jpeg_quality,
        interval=args.camera_cache_interval,
    )
    stop_event = threading.Event()
    threading.Thread(
        target=serial_loop,
        args=(args.serial_port, args.baud, state, stop_event),
        daemon=True,
    ).start()
    threading.Thread(
        target=camera_cache.run_forever,
        args=(stop_event,),
        daemon=True,
    ).start()
    run_http(args.host, args.port, state, camera_cache)


if __name__ == "__main__":
    main()
