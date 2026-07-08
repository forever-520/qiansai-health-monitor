#!/usr/bin/env python3
"""Offline package integrity check for the RK Web UI bundle."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent

REQUIRED_FILES = [
    "README.md",
    "rk_acceptance_checklist.md",
    "external_access_templates.md",
    "server.py",
    "self_check.py",
    "package_check.py",
    "lubancat_rk_gateway.py",
    "start_web.sh",
    "start_web_secure.sh",
    "start_rk_stack.sh",
    "requirements-rk.txt",
    "start_web_pc.bat",
    "start_web_pc_lan.bat",
    "start_web_pc_secure.bat",
    "install_systemd.sh",
    "uninstall_systemd.sh",
    "rk_web_ui.env.example",
    "rk_stack.env.example",
    "rk_web_ui.service",
    "cloudflared_config.yml.example",
    "frpc.ini.example",
    "ui/index.html",
    "ui/styles.css",
    "ui/app.js",
    "ui/bedside_display_ref.png",
    "ui/bedside_imaging_ref.png",
]

REQUIRED_TEXT = {
    "README.md": [
        "python self_check.py",
        "WEB_USER=admin WEB_PASS=strong-password ./start_web_secure.sh",
        "external_access_templates.md",
    ],
    "rk_web_ui.env.example": [
        "WEB_USER=admin",
        "WEB_PASS=change-this-password",
        "PORT=8081",
    ],
    "rk_stack.env.example": [
        "DATA_MODE=gateway",
        "LUBANCAT_HOST=auto",
        "LUBANCAT_PREFERRED_HOSTS=",
        "REMOTE_RADAR_URL=auto",
        "GATEWAY_HTTP_PORT=8000",
    ],
    "rk_web_ui.service": [
        "ExecStart=/usr/bin/python3",
        "Restart=always",
    ],
    "cloudflared_config.yml.example": [
        "service: http://127.0.0.1:8081",
    ],
    "frpc.ini.example": [
        "local_port = 8081",
    ],
}


def main() -> int:
    ok = True
    for rel in REQUIRED_FILES:
        path = ROOT / rel
        if path.is_file():
            print(f"file {rel}: OK")
        else:
            print(f"file {rel}: MISSING")
            ok = False

    for rel, snippets in REQUIRED_TEXT.items():
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for snippet in snippets:
            if snippet in text:
                print(f"text {rel}: {snippet!r} OK")
            else:
                print(f"text {rel}: {snippet!r} MISSING")
                ok = False

    print("package check OK" if ok else "package check FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
