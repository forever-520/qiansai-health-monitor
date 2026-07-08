#!/usr/bin/env python3
"""Static Web UI server with optional Basic Auth."""

from __future__ import annotations

import base64
import functools
import hashlib
import http.server
import json
import math
import os
import secrets
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent / "ui"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).resolve().parent / "data"
CARE_RECORDS_PATH = Path(os.environ.get("CARE_RECORDS_PATH", DATA_DIR / "care_records.json"))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.care_logic import evaluate_care

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8081"))
WEB_USER = os.environ.get("WEB_USER", "admin")
WEB_PASS = os.environ.get("WEB_PASS", "")
DATA_MODE = os.environ.get("DATA_MODE", "mock").strip().lower()
DATA_HTTP_URL = os.environ.get("DATA_HTTP_URL", "http://127.0.0.1:8000").rstrip("/")
DATA_WS_URL = os.environ.get("DATA_WS_URL", "ws://127.0.0.1:8001/ws")
CAMERA_PROXY_TIMEOUT = float(os.environ.get("CAMERA_PROXY_TIMEOUT", "40.0"))
SESSION_COOKIE = "rk_web_auth"
ACTIVE_SESSIONS: set[str] = set()


class AuthHandler(http.server.SimpleHTTPRequestHandler):
    def _basic_authorized(self) -> bool:
        token = base64.b64encode(f"{WEB_USER}:{WEB_PASS}".encode("utf-8")).decode("ascii")
        return self.headers.get("Authorization") == f"Basic {token}"

    def _cookie_authorized(self) -> bool:
        if not WEB_PASS:
            return True
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE and value in ACTIVE_SESSIONS:
                return True
        return False

    def _authorized(self, allow_basic: bool = False) -> bool:
        if not WEB_PASS:
            return True
        if self._cookie_authorized():
            return True
        return allow_basic and self._basic_authorized()

    def end_headers(self) -> None:
        super().end_headers()

    def do_GET(self) -> None:
        request_path = urllib.parse.urlparse(self.path).path
        allow_basic = request_path in ("/healthz", "/radar/raw", "/yolo/status", "/care/records", "/tunnel-url")
        public_asset = request_path == "/health_logo.png"
        if public_asset:
            super().do_GET()
            return
        if not self._authorized(allow_basic=allow_basic):
            if request_path == "/healthz" or request_path == "/ws":
                self._send_unauthorized()
            else:
                self._handle_login()
            return
        if request_path == "/logout":
            self._handle_logout()
            return
        if request_path == "/login":
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if request_path == "/ws":
            self._handle_websocket()
            return
        if request_path == "/radar/raw":
            self._handle_radar_raw()
            return
        if request_path == "/care/records":
            self._handle_care_records_get()
            return
        if request_path == "/yolo/status":
            self._handle_yolo_status()
            return
        if request_path == "/tunnel-url":
            self._handle_tunnel_url()
            return
        if request_path == "/healthz":
            self._handle_health()
            return
        if request_path == "/" or request_path == "/index.html":
            self._handle_index()
            return
        super().do_GET()

    def do_POST(self) -> None:
        request_path = urllib.parse.urlparse(self.path).path
        if request_path == "/camera/capture":
            if not self._authorized(allow_basic=True):
                self._send_unauthorized()
                return
            self._handle_camera_capture()
            return
        if request_path == "/yolo/detect":
            if not self._authorized(allow_basic=True):
                self._send_unauthorized()
                return
            self._handle_yolo_detect()
            return
        if request_path == "/care/records":
            if not self._authorized(allow_basic=True):
                self._send_unauthorized()
                return
            self._handle_care_records_post()
            return
        if request_path != "/login":
            self.send_error(404, "File not found")
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        fields = urllib.parse.parse_qs(body)
        user = fields.get("username", [""])[0]
        password = fields.get("password", [""])[0]
        if WEB_PASS and user == WEB_USER and password == WEB_PASS:
            token = secrets.token_urlsafe(32)
            ACTIVE_SESSIONS.add(token)
            self.send_response(302)
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}={token}; Path=/; SameSite=Strict; HttpOnly")
            self.send_header("Location", "/")
            self.end_headers()
            return
        self._handle_login(error=True)

    def _handle_camera_capture(self) -> None:
        if DATA_MODE not in {"gateway", "rk3576_slave", "real"}:
            self.send_error(503, "Camera capture requires DATA_MODE=gateway")
            return
        try:
            req = urllib.request.Request(f"{DATA_HTTP_URL}/camera/capture", method="POST")
            with urllib.request.urlopen(req, timeout=CAMERA_PROXY_TIMEOUT) as response:
                data = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/octet-stream")
        except urllib.error.HTTPError as exc:
            data = exc.read()
            status = exc.code
            content_type = exc.headers.get("Content-Type", "application/json; charset=utf-8")
        except Exception as exc:
            payload = json.dumps({"ok": False, "error": f"gateway_unavailable:{exc}"}, separators=(",", ":")).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_radar_raw(self) -> None:
        if DATA_MODE not in {"gateway", "rk3576_slave", "real"}:
            self.send_error(503, "Radar data requires DATA_MODE=gateway")
            return
        try:
            with urllib.request.urlopen(f"{DATA_HTTP_URL}/radar/raw", timeout=3) as response:
                data = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json; charset=utf-8")
        except Exception as exc:
            data = json.dumps({"ok": False, "error": f"gateway_unavailable:{exc}"}, separators=(",", ":")).encode("utf-8")
            status = 502
            content_type = "application/json; charset=utf-8"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_tunnel_url(self) -> None:
        path = ROOT / "current_tunnel_url.txt"
        text = path.read_text(encoding="utf-8", errors="replace").strip() if path.exists() else ""
        if not text:
            text = "waiting for cloudflared tunnel..."
        data = f"{text}\n".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_care_records_get(self) -> None:
        records = self._load_care_records()
        payload = json.dumps({"ok": True, "records": records}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle_care_records_post(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            payload = json.loads(body or "{}")
            records = self._normalize_care_records(payload.get("records", []))
            self._save_care_records(records)
            data = json.dumps({"ok": True, "records": records}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            status = 200
        except Exception as exc:
            data = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            status = 400
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _load_care_records(self) -> list[dict]:
        try:
            data = json.loads(CARE_RECORDS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(data, dict):
            data = data.get("records", [])
        return self._normalize_care_records(data)

    def _save_care_records(self, records: list[dict]) -> None:
        CARE_RECORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CARE_RECORDS_PATH.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(records, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(CARE_RECORDS_PATH)

    def _normalize_care_records(self, records) -> list[dict]:
        if not isinstance(records, list):
            return []
        clean = []
        cutoff = time.time() - 30 * 24 * 60 * 60
        for item in records:
            if not isinstance(item, dict):
                continue
            iso_time = str(item.get("isoTime") or item.get("iso_time") or "")
            parsed = self._parse_iso_seconds(iso_time)
            if parsed and parsed < cutoff:
                continue
            clean.append(
                {
                    "isoTime": iso_time,
                    "displayTime": str(item.get("displayTime") or item.get("display_time") or item.get("time") or "--"),
                    "time": str(item.get("time") or "--:--:--"),
                    "text": str(item.get("text") or "护理记录")[:64],
                    "badge": str(item.get("badge") or "已记录")[:16],
                    "note": str(item.get("note") or "")[:96],
                    "signature": str(item.get("signature") or ""),
                }
            )
        clean.sort(key=lambda record: record.get("isoTime", ""), reverse=True)
        spaced = []
        for record in clean:
            record_ts = self._parse_iso_seconds(record.get("isoTime", ""))
            if any(abs(self._parse_iso_seconds(saved.get("isoTime", "")) - record_ts) < 5 * 60 for saved in spaced):
                continue
            spaced.append(record)
            if len(spaced) >= 240:
                break
        return spaced

    def _parse_iso_seconds(self, value: str) -> float:
        try:
            stamp = value.replace("Z", "+00:00")
            return datetime.fromisoformat(stamp).timestamp()
        except Exception:
            return 0.0

    def _handle_yolo_detect(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length)
        if DATA_MODE in {"gateway", "rk3576_slave", "real"}:
            try:
                req = urllib.request.Request(f"{DATA_HTTP_URL}/yolo/detect", data=body, method="POST")
                content_type = self.headers.get("Content-Type")
                if content_type:
                    req.add_header("Content-Type", content_type)
                with urllib.request.urlopen(req, timeout=20) as response:
                    data = response.read()
                    status = response.status
                    content_type = response.headers.get("Content-Type", "application/json; charset=utf-8")
            except urllib.error.HTTPError as exc:
                data = exc.read()
                status = exc.code
                content_type = exc.headers.get("Content-Type", "application/json; charset=utf-8")
            except Exception:
                data = self._yolo_placeholder("检测服务暂未连接")
                status = 200
                content_type = "application/json; charset=utf-8"
        else:
            data = self._yolo_placeholder("等待模型服务接入")
            status = 200
            content_type = "application/json; charset=utf-8"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_yolo_status(self) -> None:
        if DATA_MODE in {"gateway", "rk3576_slave", "real"}:
            try:
                with urllib.request.urlopen(f"{DATA_HTTP_URL}/yolo/status", timeout=3) as response:
                    data = response.read()
                    status = response.status
                    content_type = response.headers.get("Content-Type", "application/json; charset=utf-8")
            except Exception:
                data = json.dumps(
                    {
                        "ok": True,
                        "enabled": False,
                        "bedOccupied": None,
                        "confidence": 0,
                        "source": "reserved",
                        "message": "YOLO接口待接入",
                        "ts": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                status = 200
                content_type = "application/json; charset=utf-8"
        else:
            data = json.dumps(
                {
                    "ok": True,
                    "enabled": False,
                    "bedOccupied": None,
                    "confidence": 0,
                    "source": "mock",
                    "message": "YOLO接口待接入",
                    "ts": int(time.time() * 1000),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            status = 200
            content_type = "application/json; charset=utf-8"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _yolo_placeholder(self, message: str) -> bytes:
        return json.dumps(
            {
                "ok": True,
                "enabled": False,
                "bedOccupied": None,
                "confidence": 0,
                "maxConfidence": 0,
                "elapsedMs": 0,
                "detections": [],
                "message": message,
                "ts": int(time.time() * 1000),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def _send_unauthorized(self) -> None:
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b'{"ok":false,"error":"authentication_required"}\n')

    def _handle_logout(self) -> None:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                ACTIVE_SESSIONS.discard(value)
        self.send_response(302)
        self.send_header(f"Set-Cookie", f"{SESSION_COOKIE}=; Path=/; SameSite=Strict; HttpOnly; Max-Age=0")
        self.send_header("Location", "/login")
        self.end_headers()

    def _handle_index(self) -> None:
        path = ROOT / "index.html"
        html = path.read_text(encoding="utf-8", errors="replace")
        if WEB_PASS:
            logout = """
<a class="logout-link" href="/logout" title="Logout">Logout</a>
<style>
  .logout-link {
    position: fixed;
    top: 12px;
    right: 14px;
    z-index: 999;
    height: 30px;
    padding: 0 12px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid rgba(128, 167, 220, .45);
    background: rgba(8, 20, 36, .86);
    color: #dbe8ff;
    font: 13px "Microsoft YaHei UI", Arial, sans-serif;
    text-decoration: none;
  }
  .logout-link:hover { background: rgba(31, 115, 255, .95); color: #fff; }
</style>
"""
            html = html.replace("</body>", f"{logout}</body>")
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_login(self, error: bool = False) -> None:
        message = '<div class="error">Username or password is incorrect.</div>' if error else ""
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>健康监测 - 登录</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: "Microsoft YaHei UI", Arial, sans-serif;
      background: #f5f8fc;
      color: #071426;
    }}
    .shell {{
      width: min(420px, calc(100vw - 32px));
      border: 1px solid #dbe4f0;
      background: #ffffff;
      padding: 28px;
      box-shadow: 0 18px 45px rgba(30, 60, 100, .14);
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 24px;
    }}
    .mark {{
      width: 56px;
      height: 56px;
      display: grid;
      place-items: center;
      overflow: hidden;
      border: 0;
      background: #ffffff;
    }}
    .mark img {{
      width: 52px;
      height: 52px;
      display: block;
      object-fit: contain;
    }}
    h1 {{
      margin: 0;
      font-size: 24px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .sub {{
      margin: 4px 0 0;
      font-size: 13px;
      color: #66758c;
    }}
    label {{
      display: block;
      margin: 14px 0 7px;
      font-size: 13px;
      color: #33415c;
    }}
    input {{
      width: 100%;
      height: 42px;
      border: 1px solid #cfdbea;
      background: #fbfdff;
      color: #071426;
      padding: 0 12px;
      font-size: 15px;
      outline: none;
    }}
    input:focus {{
      border-color: #4d95ff;
      box-shadow: 0 0 0 2px rgba(77, 149, 255, .16);
    }}
    button {{
      width: 100%;
      height: 44px;
      margin-top: 20px;
      border: 0;
      background: #1f73ff;
      color: white;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }}
    button:hover {{ background: #3684ff; }}
    .error {{
      margin: 0 0 8px;
      padding: 9px 10px;
      color: #ffd9df;
      background: rgba(219, 52, 78, .18);
      border: 1px solid rgba(219, 52, 78, .35);
      font-size: 13px;
    }}
    .foot {{
      margin-top: 16px;
      color: #78889d;
      font-size: 12px;
      text-align: center;
    }}
  </style>
</head>
<body>
  <form class="shell" method="post" action="/login">
    <div class="brand">
      <div class="mark"><img src="/health_logo.png" alt="健康监测"></div>
      <div>
        <h1>健康监测</h1>
        <p class="sub">健康监测系统登录</p>
      </div>
    </div>
    {message}
    <label for="username">Username</label>
    <input id="username" name="username" autocomplete="username" value="{WEB_USER}" required>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
    <button type="submit">Login</button>
    <div class="foot">Protected local and remote monitoring dashboard</div>
  </form>
</body>
</html>"""
        data = html.encode("utf-8")
        self.send_response(401 if error else 200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_health(self) -> None:
        payload = {
            "ok": True,
            "service": "rk_web_ui",
            "auth": bool(WEB_PASS),
            "websocket": "/ws",
            "port": PORT,
            "data_mode": DATA_MODE,
            "data_http_url": DATA_HTTP_URL if DATA_MODE != "mock" else "",
            "data_ws_url": DATA_WS_URL if DATA_MODE != "mock" else "",
        }
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_error(400, "Missing Sec-WebSocket-Key")
            return
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True
        if DATA_MODE in {"gateway", "rk3576_slave", "real"}:
            self._stream_gateway_vitals()
            return
        self._stream_mock_vitals()

    def _send_ws_text(self, payload: dict) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = bytearray([0x81])
        if len(data) < 126:
            header.append(len(data))
        elif len(data) < 65536:
            header.extend((126, *struct.pack("!H", len(data))))
        else:
            header.extend((127, *struct.pack("!Q", len(data))))
        self.wfile.write(header + data)
        self.wfile.flush()

    def _recv_exact(self, sock: socket.socket, size: int) -> bytes:
        chunks = []
        remaining = size
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                raise ConnectionError("websocket upstream closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _open_upstream_ws(self) -> socket.socket:
        parsed = urllib.parse.urlparse(DATA_WS_URL)
        if parsed.scheme != "ws" or not parsed.hostname:
            raise ValueError("DATA_WS_URL must be ws://host:port/path")
        port = parsed.port or 80
        path = parsed.path or "/ws"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        sock = socket.create_connection((parsed.hostname, port), timeout=10)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        host = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{port}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = b""
        while b"\r\n\r\n" not in response:
            response += sock.recv(4096)
            if len(response) > 65536:
                raise ConnectionError("websocket upstream handshake too large")
        if not response.startswith(b"HTTP/1.1 101") and not response.startswith(b"HTTP/1.0 101"):
            raise ConnectionError(response.split(b"\r\n", 1)[0].decode("ascii", "replace"))
        return sock

    def _read_ws_frame(self, sock: socket.socket) -> bytes:
        head = self._recv_exact(sock, 2)
        length = head[1] & 0x7F
        extra = b""
        if length == 126:
            extra = self._recv_exact(sock, 2)
            length = struct.unpack("!H", extra)[0]
        elif length == 127:
            extra = self._recv_exact(sock, 8)
            length = struct.unpack("!Q", extra)[0]
        mask = b""
        if head[1] & 0x80:
            mask = self._recv_exact(sock, 4)
        payload = self._recv_exact(sock, length)
        return head + extra + mask + payload

    def _stream_gateway_vitals(self) -> None:
        try:
            upstream = self._open_upstream_ws()
        except Exception as exc:
            self._send_ws_text({"type": "stats", "frame_count": 0, "parser_err": 0, "crc_err": 0, "online": False})
            self._send_ws_text({"type": "error", "detail": f"gateway_ws_unavailable:{exc}"})
            return
        with upstream:
            while True:
                try:
                    frame = self._read_ws_frame(upstream)
                    self.wfile.write(frame)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError, OSError, ConnectionError):
                    return

    def _stream_mock_vitals(self) -> None:
        frame = 28267
        start = time.monotonic()
        while True:
            t = time.monotonic() - start
            hr = 78 + math.sin(t * 0.9) * 4
            br = 16 + math.sin(t * 0.55) * 1.5
            motion = int(35 + math.sin(t * 0.35) * 10)
            care = evaluate_care(
                exist=1,
                bed=1,
                sleep_state=2,
                motion_val=motion,
                heart_rate=hr,
                breath_rate=br,
                frame_count=frame,
                online=True,
            )
            heart = [int(128 + math.sin((i / 64) * math.tau + t * 4.0) * 54) for i in range(64)]
            breath = [int(128 + math.sin((i / 64) * math.tau + t * 1.2) * 38) for i in range(64)]
            try:
                # PC/RK 初期联调用模拟数据，后续真实雷达数据接入时保持同一 JSON 格式。
                self._send_ws_text({
                    "type": "vital_signs",
                    "data": {
                        "hr": round(hr, 1),
                        "br": round(br, 1),
                        "motion": motion,
                        "presence": "有人",
                        "stability": "稳定",
                        "bedState": "在床",
                        "heartValid": True,
                        "breathValid": True,
                        "online": True,
                        "care": care.to_dict(),
                    },
                })
                self._send_ws_text({"type": "stats", "frame_count": frame, "parser_err": 0, "crc_err": 0, "online": True})
                self._send_ws_text({"type": "waveform", "heart": heart, "breath": breath, "frame_count": frame})
                frame += 1
                time.sleep(1)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError, OSError):
                return


def main() -> None:
    handler = functools.partial(AuthHandler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer((HOST, PORT), handler) as server:
        auth_note = "enabled" if WEB_PASS else "disabled"
        print(f"Serving {ROOT} on http://{HOST}:{PORT} (auth: {auth_note})", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
