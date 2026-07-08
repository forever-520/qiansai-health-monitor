#!/usr/bin/env python3
"""Check that the bundled Web UI server answers HTTP and WebSocket requests."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import socket
import sys
from typing import Dict, Tuple


HOST = os.environ.get("CHECK_HOST", "127.0.0.1")
PORT = int(os.environ.get("CHECK_PORT", os.environ.get("PORT", "8081")))
CHECK_USER = os.environ.get("CHECK_USER", os.environ.get("WEB_USER", "admin"))
CHECK_PASS = os.environ.get("CHECK_PASS", os.environ.get("WEB_PASS", ""))


def auth_header() -> Dict[str, str]:
    if not CHECK_PASS:
        return {}
    token = base64.b64encode(f"{CHECK_USER}:{CHECK_PASS}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def login_cookie() -> str:
    if not CHECK_PASS:
        return ""
    conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
    body = f"username={CHECK_USER}&password={CHECK_PASS}"
    try:
        conn.request(
            "POST",
            "/login",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = conn.getresponse()
        resp.read()
        cookie = resp.getheader("Set-Cookie", "").split(";", 1)[0]
        ok = resp.status == 302 and cookie.startswith("rk_web_auth=")
        print(f"HTTP /login: status={resp.status} {'OK' if ok else 'FAIL'}")
        return cookie if ok else ""
    finally:
        conn.close()


def check_http() -> Tuple[bool, str]:
    conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
    try:
        cookie = login_cookie()
        headers = {"Cookie": cookie} if cookie else {}
        conn.request("GET", "/", headers=headers)
        resp = conn.getresponse()
        body = resp.read(4096)
        ok = resp.status == 200 and (b"<!DOCTYPE html>" in body or b"<html" in body)
        print(f"HTTP /: status={resp.status} {'OK' if ok else 'FAIL'}")
        return ok, cookie
    finally:
        conn.close()


def check_health(cookie: str) -> bool:
    conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
    headers = auth_header()
    if cookie:
        headers["Cookie"] = cookie
    try:
        conn.request("GET", "/healthz", headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            payload = {}
        ok = resp.status == 200 and payload.get("ok") is True and payload.get("websocket") == "/ws"
        print(f"HTTP /healthz: status={resp.status} {'OK' if ok else 'FAIL'}")
        return ok
    finally:
        conn.close()


def read_http_response(sock: socket.socket) -> Tuple[str, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    return head.decode("iso-8859-1"), rest


def read_ws_text(sock: socket.socket, initial: bytes) -> str:
    data = bytearray(initial)
    while len(data) < 2:
        data.extend(sock.recv(4096))

    first = data[0]
    second = data[1]
    opcode = first & 0x0F
    length = second & 0x7F
    offset = 2

    if length == 126:
        while len(data) < offset + 2:
            data.extend(sock.recv(4096))
        length = int.from_bytes(data[offset:offset + 2], "big")
        offset += 2
    elif length == 127:
        while len(data) < offset + 8:
            data.extend(sock.recv(4096))
        length = int.from_bytes(data[offset:offset + 8], "big")
        offset += 8

    while len(data) < offset + length:
        data.extend(sock.recv(4096))

    if opcode != 1:
        raise RuntimeError(f"expected text frame, got opcode {opcode}")
    return bytes(data[offset:offset + length]).decode("utf-8")


def check_ws(cookie: str) -> bool:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET /ws HTTP/1.1\r\n"
        f"Host: {HOST}:{PORT}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
    )
    if cookie:
        request += f"Cookie: {cookie}\r\n"
    request += "\r\n"

    with socket.create_connection((HOST, PORT), timeout=5) as sock:
        sock.settimeout(5)
        sock.sendall(request.encode("ascii"))
        head, rest = read_http_response(sock)
        status = head.splitlines()[0] if head else ""
        if " 101 " not in status:
            print(f"WebSocket /ws: {status or 'no response'} FAIL")
            return False

        expected_accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        headers: Dict[str, str] = {}
        for line in head.split("\r\n")[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.lower()] = value.strip()
        if headers.get("sec-websocket-accept") != expected_accept:
            print("WebSocket /ws: bad accept header FAIL")
            return False

        payload = json.loads(read_ws_text(sock, rest))
        ok = payload.get("type") == "vital_signs" and "data" in payload
        print(f"WebSocket /ws: first_type={payload.get('type')} {'OK' if ok else 'FAIL'}")
        return ok


def main() -> int:
    print(f"Checking http://{HOST}:{PORT}")
    try:
        http_ok, cookie = check_http()
        return 0 if http_ok and check_health(cookie) and check_ws(cookie) else 1
    except Exception as exc:
        print(f"self-check failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
