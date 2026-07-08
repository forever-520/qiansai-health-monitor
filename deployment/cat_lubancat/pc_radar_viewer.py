import argparse
import json
import os
import time
from urllib.request import urlopen


def fetch_json(url, timeout=3):
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def format_display(data):
    pos = data.get("position_cm", {})
    return "\n".join(
        [
            "R60ABD1 radar live data",
            f"frames: {data.get('frames', 0)}",
            f"human: {data.get('human_exist', '-')} | motion: {data.get('motion', '-')}",
            f"distance: {data.get('distance_cm', '-')} cm | pos: x={pos.get('x', '-')} y={pos.get('y', '-')} z={pos.get('z', '-')}",
            f"heart: {data.get('heart_bpm', 0)} bpm | breath: {data.get('breath_rpm', 0)} rpm ({data.get('breath_state', '-')})",
            f"bed: {data.get('bed', '-')} | sleep: {data.get('sleep', '-')}",
            f"errors: checksum={data.get('checksum_errors', 0)} parse={data.get('parse_errors', 0)}",
            f"last: {data.get('last_frame_hex', '')}",
        ]
    )


def build_parser():
    parser = argparse.ArgumentParser(description="Display LubanCat radar JSON data on PC.")
    parser.add_argument("--url", default="http://192.168.31.73:8000/radar")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    return parser


def main():
    args = build_parser().parse_args()
    while True:
        try:
            data = fetch_json(args.url)
            os.system("cls" if os.name == "nt" else "clear")
            print(format_display(data))
        except Exception as exc:
            print(f"read failed: {exc}")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
