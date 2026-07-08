#!/usr/bin/env python3
"""
WebSocket Bridge Server — 连接 radar C 后端与 Web UI 前端

作用：
  在开发阶段模拟 R60ABD1 雷达后端，生成真实感生命体征数据，
  通过 WebSocket 推送至 Web UI，替代前端硬编码的 mock 数据。

启动：
  python ws_bridge_server.py [--port 8080] [--hr 72] [--br 16] [--rate 30]

依赖：
  pip install websockets asyncio

与 C 后端集成时，将此服务器的数据源替换为
  r60abd1.c / vitals_ring_buffer 中的真实采样值即可。
"""

import argparse
import asyncio
import json
import math
import random
import time

try:
    import websockets
except ImportError:
    raise SystemExit("缺少依赖: pip install websockets")

# ---------------------------------------------------------------------------
# 波形生成器 — 模拟 R60ABD1 的 DP5/DP7（心率波）和 DP5/DP10（呼吸波）
# ---------------------------------------------------------------------------

class WaveformGenerator:
    """生成带生理变异的合成波形，供 UI 开发阶段使用。"""

    def __init__(self, heart_rate: float, breath_rate: float, sample_rate: float = 30.0):
        self.hr = heart_rate          # bpm
        self.br = breath_rate          # rpm
        self.sr = sample_rate          # Hz
        self.phase = 0.0
        self._last_tick = time.monotonic()

    def tick(self, dt: float | None = None) -> tuple[list[float], list[float]]:
        """返回 (heart_wave_512, breath_wave_512)，中心值 128，范围 ±48。"""
        if dt is None:
            now = time.monotonic()
            dt = now - self._last_tick
            self._last_tick = now

        self.phase += dt * 2 * math.pi * 1.0  # 基线 1 Hz 旋转

        n = 512
        heart = [0.0] * n
        breath = [0.0] * n

        # 心率波：主频 HR/60 Hz + 二次谐波 + 微小随机颤动
        hr_freq = self.hr / 60.0
        for i in range(n):
            t = i / self.sr + self.phase
            h = (math.sin(t * 2 * math.pi * hr_freq) * 36 +
                 math.sin(t * 2 * math.pi * hr_freq * 2) * 9 +
                 math.sin(t * 2 * math.pi * 0.15) * 3)  # 低频漂移
            heart[i] = round(128 + h + random.uniform(-1.0, 1.0))

        # 呼吸波：主频 BR/60 Hz + 缓慢基线漂移
        br_freq = self.br / 60.0
        for i in range(n):
            t = i / self.sr + self.phase * 0.3
            b = (math.sin(t * 2 * math.pi * br_freq) * 42 +
                 math.sin(t * 2 * math.pi * 0.08) * 6)  # 缓慢漂移
            breath[i] = round(128 + b + random.uniform(-0.5, 0.5))

        return heart, breath


# ---------------------------------------------------------------------------
# 生理信号模拟器 — 模拟雷达处理后的生命体征输出
# ---------------------------------------------------------------------------

class VitalSimulator:
    """模拟 RealtimeState + AlarmEvent 更新。"""

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

        # 自然生理变异：缓慢漂移 + 小幅正弦波动
        hr_now = (self.hr +
                  math.sin(self._walk_phase * 0.7) * 2.5 +
                  self._hr_drift)
        br_now = (self.br +
                  math.sin(self._walk_phase * 0.4 + 1.2) * 1.8 +
                  self._br_drift)

        self._hr_drift += random.uniform(-0.05, 0.05)
        self._br_drift += random.uniform(-0.03, 0.03)
        self._hr_drift = max(-0.8, min(0.8, self._hr_drift))
        self._br_drift = max(-0.4, min(0.4, self._br_drift))

        motion_now = (self.motion +
                      math.sin(self._walk_phase * 1.2) * 8 +
                      random.uniform(-2, 2))
        motion_now = max(0, min(100, motion_now))

        self.frame_count += 2
        if random.random() < 0.0005:
            self.parser_err += 1
        if random.random() < 0.0002:
            self.crc_err += 1

        return {
            "hr": round(hr_now, 1),
            "br": round(br_now, 1),
            "motion": round(motion_now, 0),
            "presence": self.presence,
            "stability": self.stability,
            "online": self.online,
        }


# ---------------------------------------------------------------------------
# WebSocket 服务器
# ---------------------------------------------------------------------------

class VitalBridgeServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8080,
                 hr: float = 72.0, br: float = 16.0, rate: float = 30.0):
        self.host = host
        self.port = port
        self.rate = rate
        self.clients: set[websockets.WebSocketServerProtocol] = set()

        self.wave_gen = WaveformGenerator(hr, br, rate)
        self.vital_sim = VitalSimulator(hr, br)

        # 告警池：周期性注入变化
        self._alarm_cycle = 0

    async def handler(self, ws: websockets.WebSocketServerProtocol):
        self.clients.add(ws)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    await self._handle_command(ws, msg)
                except (json.JSONDecodeError, KeyError):
                    await ws.send(json.dumps({
                        "type": "error",
                        "detail": "无效消息格式"
                    }, ensure_ascii=False))
        finally:
            self.clients.discard(ws)

    async def _handle_command(self, ws, msg: dict):
        cmd = msg.get("cmd", "")
        data = msg.get("data", {})

        if cmd == "set_config":
            if "hr" in data:
                self.vital_sim.hr = float(data["hr"])
                self.wave_gen.hr = float(data["hr"])
            if "br" in data:
                self.vital_sim.br = float(data["br"])
                self.wave_gen.br = float(data["br"])
            await ws.send(json.dumps({
                "type": "config_ack",
                "hr": self.vital_sim.hr,
                "br": self.vital_sim.br,
            }))
        elif cmd == "ping":
            await ws.send(json.dumps({"type": "pong"}))

    async def broadcast(self, msg: dict):
        if not self.clients:
            return
        payload = json.dumps(msg, ensure_ascii=False)
        await asyncio.gather(
            *(c.send(payload) for c in self.clients.copy()),
            return_exceptions=True
        )

    async def tick_loop(self):
        """主循环：按 self.rate 频率推送生理数据 + 波形。"""
        interval = 1.0 / self.rate
        while True:
            t0 = time.monotonic()
            ts = int(time.time() * 1000)
            dt = interval

            # ── 生命体征 ──
            vital = self.vital_sim.update(dt)
            await self.broadcast({
                "type": "vital_signs",
                "ts": ts,
                "data": vital,
            })

            # ── 波形（每 tick 推送 512 点窗口） ──
            heart_wave, breath_wave = self.wave_gen.tick(dt)
            await self.broadcast({
                "type": "waveform",
                "ts": ts,
                "heart": heart_wave,
                "breath": breath_wave,
            })

            # ── 统计 ──
            await self.broadcast({
                "type": "stats",
                "ts": ts,
                "frame_count": self.vital_sim.frame_count,
                "parser_err": self.vital_sim.parser_err,
                "crc_err": self.vital_sim.crc_err,
                "online": self.vital_sim.online,
            })

            # ── 告警（周期性检查） ──
            self._alarm_cycle += 1
            if self._alarm_cycle % (self.rate * 10) == 0:  # 每 10 秒
                alarms = self._build_alarms(vital)
                await self.broadcast({
                    "type": "alarms",
                    "ts": ts,
                    "alarms": alarms,
                })

            # ── 精准节流 ──
            elapsed = time.monotonic() - t0
            sleep = interval - elapsed
            if sleep > 0:
                await asyncio.sleep(sleep)

    def _build_alarms(self, vital: dict) -> list[dict]:
        now_s = time.strftime("%H:%M:%S")
        alarms = []

        hr = vital["hr"]
        br = vital["br"]
        motion = vital["motion"]

        if hr > 100 or hr < 50:
            alarms.append({
                "level": "error",
                "title": "心率超限",
                "time": now_s,
                "detail": f"{hr} bpm，超出配置范围",
            })
        elif hr > 90 or hr < 55:
            alarms.append({
                "level": "warn",
                "title": "心率边缘",
                "time": now_s,
                "detail": f"{hr} bpm，趋近阈值",
            })

        if br > 24 or br < 8:
            alarms.append({
                "level": "error",
                "title": "呼吸率超限",
                "time": now_s,
                "detail": f"{br} rpm，超出配置范围",
            })

        if motion < 5:
            alarms.append({
                "level": "warn",
                "title": "长时间低体动",
                "time": now_s,
                "detail": "体动强度连续低于阈值，等待二次确认",
            })

        if not alarms:
            alarms.append({
                "level": "normal",
                "title": "系统正常",
                "time": now_s,
                "detail": "所有指标在配置范围内",
            })

        return alarms

    async def start(self):
        async with websockets.serve(self.handler, self.host, self.port):
            print(f"[ws_bridge] 服务器已启动 ws://{self.host}:{self.port}")
            print(f"[ws_bridge] 心率={self.vital_sim.hr:.0f} bpm, "
                  f"呼吸率={self.vital_sim.br:.0f} rpm, "
                  f"推送频率={self.rate:.0f} Hz")
            await self.tick_loop()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Health Monitor WebSocket Bridge Server"
    )
    parser.add_argument("--port", type=int, default=8080,
                        help="WebSocket 端口号（默认 8080）")
    parser.add_argument("--hr", type=float, default=72.0,
                        help="初始心率 bpm（默认 72）")
    parser.add_argument("--br", type=float, default=16.0,
                        help="初始呼吸率 rpm（默认 16）")
    parser.add_argument("--rate", type=float, default=30.0,
                        help="推送频率 Hz（默认 30）")
    parser.add_argument("--host", type=str, default="0.0.0.0",
                        help="监听地址（默认 0.0.0.0）")
    args = parser.parse_args()

    server = VitalBridgeServer(
        host=args.host,
        port=args.port,
        hr=args.hr,
        br=args.br,
        rate=args.rate,
    )

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\n[ws_bridge] 服务器已停止")


if __name__ == "__main__":
    main()
