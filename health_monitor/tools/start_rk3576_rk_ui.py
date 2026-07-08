#!/usr/bin/env python3
"""启动 RK 网关 + health_monitor UI。"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Start RK gateway and health_monitor UI")
    parser.add_argument("--rk3576_slave-host", default=os.environ.get("RK3576_SLAVE_HOST", "rk3576-slave.local"))
    parser.add_argument("--rk3576_slave-port", type=int, default=int(os.environ.get("RK3576_SLAVE_PORT", "9001")))
    parser.add_argument("--ws-port", type=int, default=int(os.environ.get("GATEWAY_WS_PORT", "8001")))
    parser.add_argument("--http-port", type=int, default=int(os.environ.get("GATEWAY_HTTP_PORT", "8000")))
    parser.add_argument("--rate", type=float, default=float(os.environ.get("GATEWAY_RATE", "20.0")))
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    gateway = root / "tools" / "rk3576_rk_gateway.py"
    cmd = [
        sys.executable,
        str(gateway),
        "--rk3576_slave-host", args.rk3576_slave_host,
        "--rk3576_slave-port", str(args.rk3576_slave_port),
        "--ws-port", str(args.ws_port),
        "--http-port", str(args.http_port),
        "--rate", str(args.rate),
    ]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
