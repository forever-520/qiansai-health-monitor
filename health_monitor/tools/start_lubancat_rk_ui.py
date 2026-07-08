#!/usr/bin/env python3
"""启动 RK 网关 + health_monitor UI。"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Start RK gateway and health_monitor UI")
    parser.add_argument("--lubancat-host", default=os.environ.get("LUBANCAT_HOST", "lubancat.local"))
    parser.add_argument("--lubancat-port", type=int, default=int(os.environ.get("LUBANCAT_PORT", "9001")))
    parser.add_argument("--ws-port", type=int, default=int(os.environ.get("GATEWAY_WS_PORT", "8001")))
    parser.add_argument("--http-port", type=int, default=int(os.environ.get("GATEWAY_HTTP_PORT", "8000")))
    parser.add_argument("--rate", type=float, default=float(os.environ.get("GATEWAY_RATE", "20.0")))
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    gateway = root / "tools" / "lubancat_rk_gateway.py"
    cmd = [
        sys.executable,
        str(gateway),
        "--lubancat-host", args.lubancat_host,
        "--lubancat-port", str(args.lubancat_port),
        "--ws-port", str(args.ws_port),
        "--http-port", str(args.http_port),
        "--rate", str(args.rate),
    ]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
