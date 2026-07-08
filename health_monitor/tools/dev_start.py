#!/usr/bin/env python3
"""
Health Monitor 一站式开发启动脚本。

同时启动：
  1) WebSocket 桥接服务器（模拟雷达后端） → ws://localhost:8080
  2) HTTP 静态文件服务器（UI 前端）    → http://localhost:8081

使用方式：
  cd health_monitor
  pip install websockets
  python tools/dev_start.py [--hr 72] [--br 16] [--rate 30]

浏览器打开 http://localhost:8081 即可实时查看 UI。
关闭终端自动停止所有服务。
"""

import argparse
import asyncio
import json
import os
import sys
import time
import math
import random
import threading
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# ---- 确保能导入 ws_bridge_server ----
sys.path.insert(0, str(Path(__file__).resolve().parent))
# 直接把 ws_bridge_server 的核心类复制过来，避免跨文件导入问题
# (如果 ws_bridge_server.py 与 dev_start.py 同目录，直接 import 即可)

# ────────────────────────────────────────────────────────────
# WebSocket 桥接服务器（内联实现，与 ws_bridge_server.py 逻辑一致）
# ────────────────────────────────────────────────────────────

class WaveformGenerator:
    def __init__(self, heart_rate: float, breath_rate: float, sample_rate: float = 30.0):
        self.hr = heart_rate
        self.br = breath_rate
        self.sr = sample_rate
        self.phase = 0.0
        self._last_tick = time.monotonic()

    def tick(self, dt: float | None = None):
        if dt is None:
            now = time.monotonic()
            dt = now - self._last_tick
            self._last_tick = now
        self.phase += dt * 2 * math.pi * 1.0
        n = 512
        heart, breath = [0.0] * n, [0.0] * n
        hr_freq = self.hr / 60.0
        for i in range(n):
            t = i / self.sr + self.phase
            h = (math.sin(t * 2 * math.pi * hr_freq) * 36 +
                 math.sin(t * 2 * math.pi * hr_freq * 2) * 9 +
                 math.sin(t * 2 * math.pi * 0.15) * 3)
            heart[i] = round(128 + h + random.uniform(-1.0, 1.0))
        br_freq = self.br / 60.0
        for i in range(n):
            t = i / self.sr + self.phase * 0.3
            b = (math.sin(t * 2 * math.pi * br_freq) * 42 +
                 math.sin(t * 2 * math.pi * 0.08) * 6)
            breath[i] = round(128 + b + random.uniform(-0.5, 0.5))
        return heart, breath


class VitalSimulator:
    def __init__(self, hr: float = 72.0, br: float = 16.0):
        self.hr = hr
        self.br = br
        self.motion = 26.0
        self.presence = "有人"
        self.stability = "稳定"
        self.frame_count = 184260
        self.parser_err = 12
        self.crc_err = 3
        self.online = True
        self._hr_drift = random.uniform(-0.3, 0.3)
        self._br_drift = random.uniform(-0.1, 0.1)
        self._walk_phase = 0.0

    def update(self, dt: float) -> dict:
        self._walk_phase += dt * 0.4
        hr_now = self.hr + math.sin(self._walk_phase * 0.7) * 2.5 + self._hr_drift
        br_now = self.br + math.sin(self._walk_phase * 0.4 + 1.2) * 1.8 + self._br_drift
        self._hr_drift += random.uniform(-0.05, 0.05)
        self._br_drift += random.uniform(-0.03, 0.03)
        self._hr_drift = max(-0.8, min(0.8, self._hr_drift))
        self._br_drift = max(-0.4, min(0.4, self._br_drift))
        motion_now = max(0, min(100, self.motion + math.sin(self._walk_phase * 1.2) * 8 + random.uniform(-2, 2)))
        self.frame_count += 2
        if random.random() < 0.0005:
            self.parser_err += 1
        if random.random() < 0.0002:
            self.crc_err += 1
        return {
            "hr": round(hr_now, 1), "br": round(br_now, 1),
            "motion": round(motion_now, 0),
            "presence": self.presence, "stability": self.stability, "online": self.online,
        }


class VitalBridgeServer:
    def __init__(self, host="0.0.0.0", port=8080, hr=72.0, br=16.0, rate=30.0):
        self.host = host
        self.port = port
        self.rate = rate
        self._wave_gen = WaveformGenerator(hr, br, rate)
        self._vital_sim = VitalSimulator(hr, br)
        self._alarm_cycle = 0
        self._clients = set()

    async def _handler(self, ws):
        self._clients.add(ws)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    cmd = msg.get("cmd", "")
                    data = msg.get("data", {})
                    if cmd == "set_config":
                        if "hr" in data:
                            self._vital_sim.hr = float(data["hr"])
                            self._wave_gen.hr = float(data["hr"])
                        if "br" in data:
                            self._vital_sim.br = float(data["br"])
                            self._wave_gen.br = float(data["br"])
                        await ws.send(json.dumps({"type": "config_ack", "hr": self._vital_sim.hr, "br": self._vital_sim.br}))
                    elif cmd == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                except (json.JSONDecodeError, KeyError):
                    await ws.send(json.dumps({"type": "error", "detail": "无效消息格式"}))
        finally:
            self._clients.discard(ws)

    async def _broadcast(self, msg: dict):
        if not self._clients:
            return
        payload = json.dumps(msg, ensure_ascii=False)
        await asyncio.gather(*(c.send(payload) for c in self._clients.copy()), return_exceptions=True)

    def _build_alarms(self, vital: dict) -> list[dict]:
        now_s = time.strftime("%H:%M:%S")
        alarms = []
        hr, br, motion = vital["hr"], vital["br"], vital["motion"]
        if hr > 100 or hr < 50:
            alarms.append({"level": "error", "title": "心率超限", "time": now_s, "detail": f"{hr} bpm，超出配置范围"})
        elif hr > 90 or hr < 55:
            alarms.append({"level": "warn", "title": "心率边缘", "time": now_s, "detail": f"{hr} bpm，趋近阈值"})
        if br > 24 or br < 8:
            alarms.append({"level": "error", "title": "呼吸率超限", "time": now_s, "detail": f"{br} rpm，超出配置范围"})
        if motion < 5:
            alarms.append({"level": "warn", "title": "长时间低体动", "time": now_s, "detail": "体动强度连续低于阈值，等待二次确认"})
        if not alarms:
            alarms.append({"level": "normal", "title": "系统正常", "time": now_s, "detail": "所有指标在配置范围内"})
        return alarms

    async def _tick_loop(self):
        interval = 1.0 / self.rate
        while True:
            t0 = time.monotonic()
            ts = int(time.time() * 1000)
            dt = interval

            vital = self._vital_sim.update(dt)
            await self._broadcast({"type": "vital_signs", "ts": ts, "data": vital})

            heart_wave, breath_wave = self._wave_gen.tick(dt)
            await self._broadcast({"type": "waveform", "ts": ts, "heart": heart_wave, "breath": breath_wave})

            await self._broadcast({"type": "stats", "ts": ts,
                "frame_count": self._vital_sim.frame_count,
                "parser_err": self._vital_sim.parser_err,
                "crc_err": self._vital_sim.crc_err,
                "online": self._vital_sim.online})

            self._alarm_cycle += 1
            if self._alarm_cycle % (self.rate * 10) == 0:
                alarms = self._build_alarms(vital)
                await self._broadcast({"type": "alarms", "ts": ts, "alarms": alarms})

            elapsed = time.monotonic() - t0
            sleep = interval - elapsed
            if sleep > 0:
                await asyncio.sleep(sleep)

    async def start(self):
        try:
            import websockets
        except ImportError:
            raise SystemExit("缺少依赖: pip install websockets")
        async with websockets.serve(self._handler, self.host, self.port):
            print(f"  WebSocket Bridge → ws://{self.host}:{self.port}  （模拟雷达后端）")
            await self._tick_loop()


# ────────────────────────────────────────────────────────────
# HTTP 静态文件服务器（UI 前端）
# ────────────────────────────────────────────────────────────

class _QuietHandler(SimpleHTTPRequestHandler):
    """静默版 handler，不打印每次请求的日志。"""
    def log_message(self, fmt, *args):
        pass

def start_http_server(ui_dir: str, port: int):
    os.chdir(ui_dir)
    server = HTTPServer(("0.0.0.0", port), _QuietHandler)
    print(f"  HTTP Static Files  → http://localhost:{port}  （UI 前端）")
    server.serve_forever()


# ────────────────────────────────────────────────────────────
# 入口
# ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Health Monitor 开发启动脚本")
    parser.add_argument("--hr", type=float, default=72.0, help="初始心率 bpm (默认 72)")
    parser.add_argument("--br", type=float, default=16.0, help="初始呼吸率 rpm (默认 16)")
    parser.add_argument("--rate", type=float, default=30.0, help="推送频率 Hz (默认 30)")
    parser.add_argument("--ws-port", type=int, default=8080, help="WebSocket 端口 (默认 8080)")
    parser.add_argument("--http-port", type=int, default=8081, help="HTTP 端口 (默认 8081)")
    args = parser.parse_args()

    # 路径
    repo_root = Path(__file__).resolve().parent.parent
    ui_dir = repo_root / "src" / "ui"
    if not ui_dir.exists():
        print(f"[错误] 找不到 UI 目录: {ui_dir}")
        sys.exit(1)

    print("╔══════════════════════════════════════╗")
    print("║   Health Monitor · 开发模式          ║")
    print("╠══════════════════════════════════════╣")

    # 启动 HTTP（后台线程）
    http_thread = threading.Thread(
        target=start_http_server,
        args=(str(ui_dir), args.http_port),
        daemon=True,
    )
    http_thread.start()

    # 启动 WebSocket（主协程）
    server = VitalBridgeServer(
        host="0.0.0.0",
        port=args.ws_port,
        hr=args.hr,
        br=args.br,
        rate=args.rate,
    )

    print(f"  心率={args.hr:.0f} bpm → 呼吸率={args.br:.0f} rpm → {args.rate:.0f} Hz")
    print("╠══════════════════════════════════════╣")
    print(f"  浏览器打开: http://localhost:{args.http_port}")
    print("╚══════════════════════════════════════╝")

    # 自动打开浏览器
    try:
        webbrowser.open(f"http://localhost:{args.http_port}")
    except Exception:
        pass

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\n所有服务已停止。")


if __name__ == "__main__":
    main()
