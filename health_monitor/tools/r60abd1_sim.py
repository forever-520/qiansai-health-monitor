#!/usr/bin/env python3
"""
R60ABD1 雷达模拟器 — 嵌入式竞赛开发/测试工具

在无真实雷达硬件时模拟 R60ABD1 串口协议帧，用于测试 C 驱动（r60abd1.c）
和全数据链路（radar → webserver → UI）。

用法:
    # 输出到串口（需先安装 com0com 创建虚拟串口对）
    python r60abd1_sim.py --port COM10 --baud 115200

    # 输出到 TCP 端口（配合 socat 或修改驱动 UART 层为 TCP）
    python r60abd1_sim.py --tcp 127.0.0.1:9000

    # 输出到 stdout（管道到串口或文件）
    python r60abd1_sim.py --stdout

    # 指定场景
    python r60abd1_sim.py --port COM10 --scene exercise

场景:
    normal      — 正常状态：心率 65-80 bpm，呼吸率 14-20 bpm（默认）
    sleep       — 睡眠状态：心率 50-65 bpm，呼吸率 10-14 bpm
    exercise    — 运动状态：心率 100-140 bpm，呼吸率 24-35 bpm，高强度运动
    apnea       — 呼吸暂停模拟：呼吸率降至 0-4 bpm，心率先升后降
    motion      — 间歇运动：周期交替正常/运动
    noisy       — 数据错误注入：CRC 错误、帧丢失、乱序
"""

import argparse
import struct
import time
import math
import random
import sys
import threading

try:
    import serial
except ImportError:
    serial = None

# ====== 协议常量 ======
HEAD = b"\x53\x59"
TAIL = b"\x54\x43"
CTRL_CFG = 0x80     # 配置/心跳/运动
CTRL_BREATH = 0x81   # 呼吸
CTRL_HEART = 0x85    # 心率
CMD_HEARTBEAT = 0x01
CMD_MOTION = 0x03
CMD_RATE = 0x02
CMD_WAVEFORM = 0x05


def calc_checksum(data: bytes) -> int:
    """计算校验和：从帧头到数据段末尾所有字节累加和的低 8 位"""
    return sum(data) & 0xFF


def build_frame(control: int, command: int, payload: bytes) -> bytes:
    """构建一帧完整数据"""
    # 协议实际格式：HEAD(2) | CTRL(1) | CMD(1) | LEN(2) | DATA(LEN) | CHK(1) | TAIL(2)
    # 注意：LEN 是 data 长度，不包括 control/command/len 自身
    data_len = len(payload)
    mid = bytes([control, command, (data_len >> 8) & 0xFF, data_len & 0xFF]) + payload
    chk = calc_checksum(HEAD + mid)
    return HEAD + mid + bytes([chk]) + TAIL


# ====== 数据生成器 ======

class VitalSignsGenerator:
    """生成具有生理节奏变化的心率和呼吸率"""

    def __init__(self, base_hr=72, base_br=16, hr_var=5, br_var=3):
        self.base_hr = base_hr
        self.base_br = base_br
        self.hr_var = hr_var
        self.br_var = br_var
        self.t = random.random() * 100  # 相位偏移
        self.motion_level = 0           # 0~100
        self.presence = True

    def step(self, dt=0.1):
        """每步更新内部状态"""
        self.t += dt
        # 呼吸性窦性心律不齐模拟：心率随呼吸周期微变
        resp_sinus = 3.0 * math.sin(self.t * 2 * math.pi * (self.base_br / 60.0))
        # 低频漂移
        drift = 2.0 * math.sin(self.t * 0.1)
        # 运动影响
        motion_hr_bump = self.motion_level * 0.4

        hr = self.base_hr + resp_sinus + drift + motion_hr_bump
        hr += random.gauss(0, 0.5)
        hr = max(30, min(200, hr))

        # 呼吸率：运动时升高
        br = self.base_br + self.motion_level * 0.12
        br += random.gauss(0, 0.3)
        br = max(0, min(40, br))

        return round(hr), round(br)

    def generate_wave(self, rate_bpm: int, wave_len: int, wave_type="heart") -> bytes:
        """
        生成波形数据字节串（byte 范围 0~255，中心 128）。
        心电波：P-QRS-T 复合波形态
        呼吸波：正弦波
        """
        period_samples = int(60.0 / rate_bpm * 100)  # 假设 100 Hz
        samples = bytearray(wave_len)

        for i in range(wave_len):
            phase = (i % period_samples) / period_samples
            if wave_type == "heart":
                # 简化 ECG 形态
                val = 128.0
                # QRS 主波
                qrs_pos = 0.12
                val += 60 * math.exp(-((phase - qrs_pos) ** 2) / 0.002)
                # T 波
                t_pos = 0.35
                val += 20 * math.exp(-((phase - t_pos) ** 2) / 0.01)
                # P 波
                p_pos = 0.85
                val += 10 * math.exp(-((phase - p_pos) ** 2) / 0.008)
                # 噪声
                val += random.gauss(0, 1.5)
            else:
                # 呼吸波：近正弦，吸呼比约 1:2
                if phase < 0.35:
                    val = 128 + 50 * math.sin(phase / 0.35 * math.pi)
                else:
                    val = 128 + 50 * math.sin((phase - 0.35) / 0.65 * math.pi)
                val += random.gauss(0, 1)

            val = max(0, min(255, val))
            samples[i] = int(val)

        return bytes(samples)


# ====== 场景控制器 ======

class SceneController:
    """根据选定场景驱动 VitalSignsGenerator 的参数变化"""

    SCENES = {
        "normal":   {"hr": (65, 80), "br": (14, 20), "motion_range": (0, 5)},
        "sleep":    {"hr": (50, 65), "br": (10, 14), "motion_range": (0, 2)},
        "exercise": {"hr": (100, 140), "br": (24, 35), "motion_range": (30, 80)},
        "apnea":    {"hr": (55, 95), "br": (0, 6), "motion_range": (0, 10)},
        "motion":   {"hr": (65, 130), "br": (14, 30), "motion_range": (0, 70)},
        "noisy":    {"hr": (60, 85), "br": (12, 20), "motion_range": (0, 10)},
    }

    def __init__(self, scene="normal"):
        if scene not in self.SCENES:
            raise ValueError(f"未知场景: {scene}，可选: {list(self.SCENES.keys())}")
        self.scene = scene
        cfg = self.SCENES[scene]
        self.gen = VitalSignsGenerator(
            base_hr=random.uniform(*cfg["hr"]),
            base_br=random.uniform(*cfg["br"]),
        )
        self.t = 0.0
        self.cycle_time = 0.0

    def step(self, dt=0.1):
        self.t += dt
        self.cycle_time += dt

        if self.scene == "apnea":
            # 周期性呼吸暂停：每 20 秒一次，持续 8 秒
            cycle_pos = self.cycle_time % 28
            if cycle_pos < 20:
                self.gen.base_hr = random.uniform(60, 75)
                self.gen.base_br = random.uniform(12, 18)
            else:
                self.gen.base_hr = random.uniform(80, 95)  # 代偿性升高
                self.gen.base_br = random.uniform(0, 3)     # 几乎暂停

        elif self.scene == "motion":
            # 60 秒周期：40 秒安静 + 20 秒运动
            cycle_pos = self.cycle_time % 60
            if cycle_pos < 40:
                self.gen.base_hr = random.uniform(65, 75)
                self.gen.base_br = random.uniform(14, 18)
                self.gen.motion_level = random.uniform(0, 5)
            else:
                self.gen.base_hr = random.uniform(100, 130)
                self.gen.base_br = random.uniform(25, 30)
                self.gen.motion_level = random.uniform(30, 70)

        elif self.scene == "noisy":
            # 正常数据为主，偶发异常
            self.gen.base_hr = random.uniform(65, 80)
            self.gen.base_br = random.uniform(14, 20)

        hr, br = self.gen.step(dt)
        return hr, br


# ====== 帧发送器 ======

class FrameSender:
    """按 R60ABD1 协议周期发送各种帧"""
    # 各帧发送周期（秒）
    INTERVAL_HEARTBEAT = 1.0
    INTERVAL_HR = 1.0
    INTERVAL_BR = 1.0
    INTERVAL_MOTION = 0.5
    INTERVAL_WAVE = 1.0

    def __init__(self, send_fn, scene_controller, inject_errors=False):
        self.send = send_fn
        self.scene = scene_controller
        self.inject_errors = inject_errors
        self.timers = {
            "heartbeat": 0.0,
            "hr": 0.0,
            "br": 0.0,
            "motion": 0.0,
            "wave_heart": 0.0,
            "wave_breath": 0.0,
        }

    def _send_frame(self, control, command, payload, corrupt=False):
        frame = build_frame(control, command, payload)
        if corrupt:
            # 随机破坏一个字节
            frame_list = bytearray(frame)
            pos = random.randint(0, len(frame_list) - 1)
            frame_list[pos] = random.randint(0, 255)
            frame = bytes(frame_list)
        self.send(frame)

    def tick(self, dt, hr, br, motion):
        now = time.monotonic()

        # 心跳帧（配置帧）
        if now - self.timers["heartbeat"] >= self.INTERVAL_HEARTBEAT:
            self.timers["heartbeat"] = now
            # 心跳帧: 0x80/0x01 数据 [0x01]
            corrupt = self.inject_errors and random.random() < 0.05
            self._send_frame(CTRL_CFG, CMD_HEARTBEAT, b"\x01", corrupt=corrupt)

        # 心率数值帧
        if now - self.timers["hr"] >= self.INTERVAL_HR:
            self.timers["hr"] = now
            data = struct.pack(">B", hr)
            corrupt = self.inject_errors and random.random() < 0.08
            self._send_frame(CTRL_HEART, CMD_RATE, data, corrupt=corrupt)

        # 呼吸率数值帧
        if now - self.timers["br"] >= self.INTERVAL_BR:
            self.timers["br"] = now
            data = struct.pack(">B", br)
            corrupt = self.inject_errors and random.random() < 0.08
            self._send_frame(CTRL_BREATH, CMD_RATE, data, corrupt=corrupt)

        # 运动强度帧
        if now - self.timers["motion"] >= self.INTERVAL_MOTION:
            self.timers["motion"] = now
            data = struct.pack(">B", min(100, max(0, int(motion))))
            corrupt = self.inject_errors and random.random() < 0.05
            self._send_frame(CTRL_CFG, CMD_MOTION, data, corrupt=corrupt)

        # 心电波形帧（每 1 秒发 100 个采样点 = 1 秒 @ 100 Hz）
        if now - self.timers["wave_heart"] >= self.INTERVAL_WAVE:
            self.timers["wave_heart"] = now
            wave = self.scene.gen.generate_wave(hr, 100, "heart")
            corrupt = self.inject_errors and random.random() < 0.03
            self._send_frame(CTRL_HEART, CMD_WAVEFORM, wave, corrupt=corrupt)

        # 呼吸波形帧
        if now - self.timers["wave_breath"] >= self.INTERVAL_WAVE:
            self.timers["wave_breath"] = now
            wave = self.scene.gen.generate_wave(br, 100, "breath")
            corrupt = self.inject_errors and random.random() < 0.03
            self._send_frame(CTRL_BREATH, CMD_WAVEFORM, wave, corrupt=corrupt)


# ====== 主循环 ======

def sim_loop(sender, scene, port=None, tcp_addr=None, use_stdout=False, stop_event=None):
    """模拟器主循环"""
    target_rate = 100  # 100 Hz 更新
    dt = 1.0 / target_rate
    frame_interval = 1.0 / target_rate
    last_frame = time.monotonic()

    stats = {"frames_sent": 0, "start": time.monotonic()}
    stats_timer = time.monotonic()

    while not (stop_event and stop_event.is_set()):
        now = time.monotonic()
        elapsed = now - last_frame

        if elapsed >= frame_interval:
            last_frame = now
            hr, br = scene.step(dt)
            motion = scene.gen.motion_level
            sender.tick(dt, hr, br, motion)
            stats["frames_sent"] += 1

        # 每秒打印统计
        if now - stats_timer >= 5.0:
            stats_timer = now
            uptime = now - stats["start"]
            sys.stderr.write(
                f"\r[R60ABD1 模拟器] HR={hr} bpm  BR={br} bpm  "
                f"motion={motion:.0f}  "
                f"fps={stats['frames_sent']/uptime:.1f}  "
                f"场景={scene.scene}       "
            )
            sys.stderr.flush()

        time.sleep(0.001)  # 防忙等


def open_serial(port: str, baud: int):
    """打开串口（需要 pyserial）"""
    if serial is None:
        sys.stderr.write("错误：未安装 pyserial，请 pip install pyserial\n")
        sys.exit(1)
    return serial.Serial(port, baud, timeout=0)


def tcp_server(addr: str):
    """创建 TCP 服务器，等待一个客户端连接"""
    import socket
    host, port_str = addr.split(":")
    port = int(port_str)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    sys.stderr.write(f"[R60ABD1 模拟器] TCP 等待连接 {host}:{port}...\n")
    conn, addr = srv.accept()
    sys.stderr.write(f"[R60ABD1 模拟器] 客户端已连接 {addr}\n")
    return conn


def main():
    parser = argparse.ArgumentParser(description="R60ABD1 雷达模拟器")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--port", help="串口号，如 COM10")
    group.add_argument("--tcp", help="TCP 地址端口，如 127.0.0.1:9000")
    group.add_argument("--stdout", action="store_true", help="输出到 stdout")
    parser.add_argument("--baud", type=int, default=115200, help="串口波特率")
    parser.add_argument("--scene", default="normal",
                        choices=list(SceneController.SCENES.keys()),
                        help="模拟场景")
    parser.add_argument("--noisy", action="store_true",
                        help="注入数据错误（CRC 错误等）")
    args = parser.parse_args()

    scene = SceneController(args.scene)
    stop_event = threading.Event()

    def write_fn(data: bytes):
        if args.stdout:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        elif args.port:
            if port_handle:
                port_handle.write(data)

    def tcp_write(data: bytes):
        if tcp_conn:
            try:
                tcp_conn.sendall(data)
            except:
                pass

    port_handle = None
    tcp_conn = None

    try:
        if args.port:
            port_handle = open_serial(args.port, args.baud)
            sys.stderr.write(f"[R60ABD1 模拟器] 串口 {args.port} @ {args.baud} 已打开\n")
            sender = FrameSender(port_handle.write, scene, args.noisy)
            # 串口通常需要单独线程写
            sim_loop(sender, scene, port=args.port, stop_event=stop_event)

        elif args.tcp:
            tcp_conn = tcp_server(args.tcp)
            sender = FrameSender(tcp_write, scene, args.noisy)
            sim_loop(sender, scene, tcp_addr=args.tcp, stop_event=stop_event)

        elif args.stdout:
            sender = FrameSender(write_fn, scene, args.noisy)
            sim_loop(sender, scene, use_stdout=True, stop_event=stop_event)

    except KeyboardInterrupt:
        sys.stderr.write("\n[R60ABD1 模拟器] 用户中断\n")
    finally:
        if port_handle:
            port_handle.close()
        if tcp_conn:
            tcp_conn.close()


if __name__ == "__main__":
    main()
