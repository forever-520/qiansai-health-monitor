import argparse
import json
import math
import threading
import time
from collections import deque
from tkinter import BOTH, LEFT, RIGHT, Canvas, Frame, Label, Tk
from urllib.request import urlopen


def fetch_json(url, timeout=3):
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_wave_series(payload):
    heart = payload.get("heart", {}).get("wave", [])
    breath = payload.get("breath", {}).get("wave", [])
    frames = payload.get("system", {}).get("frame_count", 0)
    return heart, breath, frames


def scale_points(values, width, height, pad=18):
    if not values:
        return []
    usable_w = max(1, width - pad * 2)
    usable_h = max(1, height - pad * 2)
    min_v = min(values)
    max_v = max(values)
    span = max(1, max_v - min_v)
    points = []
    for i, value in enumerate(values):
        x = pad + (usable_w * i / max(1, len(values) - 1))
        y = pad + usable_h - ((value - min_v) / span) * usable_h
        points.append((x, y))
    return points


class WavePanel(Frame):
    def __init__(self, master, title, color, bg="#0B1020"):
        super().__init__(master, bg=bg, bd=0, highlightthickness=1, highlightbackground="#263246")
        self.color = color
        self.bg = bg
        self.label = Label(self, text=title, fg="white", bg=bg, font=("Microsoft YaHei UI", 12, "bold"))
        self.label.pack(anchor="w", padx=12, pady=(8, 0))
        self.canvas = Canvas(self, height=180, bg=bg, highlightthickness=0)
        self.canvas.pack(fill=BOTH, expand=True, padx=8, pady=8)
        self.values = deque(maxlen=240)

    def update_values(self, values):
        self.values = deque(values[-240:], maxlen=240)
        self.redraw()

    def redraw(self):
        self.canvas.delete("all")
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        self.canvas.create_line(12, h // 2, w - 12, h // 2, fill="#334155")
        points = scale_points(list(self.values), w, h)
        if len(points) >= 2:
            flat = [coord for point in points for coord in point]
            self.canvas.create_line(*flat, fill=self.color, width=2, smooth=True)


def build_parser():
    parser = argparse.ArgumentParser(description="Display LubanCat radar waveforms on PC.")
    parser.add_argument("--url", default="http://192.168.31.73:8000/radar/raw")
    parser.add_argument("--interval", type=float, default=1.0)
    return parser


def main():
    args = build_parser().parse_args()
    root = Tk()
    root.title("R60ABD1 Wave Monitor")
    root.geometry("900x560")
    root.configure(bg="#08111F")

    top = Frame(root, bg="#08111F")
    top.pack(fill="x", padx=12, pady=(12, 6))
    status = Label(top, text="connecting...", fg="white", bg="#08111F", font=("Microsoft YaHei UI", 11))
    status.pack(side=LEFT)
    frames = Label(top, text="", fg="#9CA3AF", bg="#08111F", font=("Consolas", 10))
    frames.pack(side=RIGHT)

    heart_panel = WavePanel(root, "Heart Wave", "#22C55E")
    heart_panel.pack(fill=BOTH, expand=True, padx=12, pady=(6, 6))
    breath_panel = WavePanel(root, "Breath Wave", "#FB7185")
    breath_panel.pack(fill=BOTH, expand=True, padx=12, pady=(6, 12))

    def tick():
        try:
            payload = fetch_json(args.url)
            heart, breath, frame_count = extract_wave_series(payload)
            heart_panel.update_values(heart)
            breath_panel.update_values(breath)
            status.config(text=f"heart={payload.get('heart', {}).get('rate', {}).get('value', 0)} bpm | breath={payload.get('breath', {}).get('rate', {}).get('value', 0)} rpm")
            frames.config(text=f"frames={frame_count} checksum={payload.get('system', {}).get('checksum_error_count', 0)} parse={payload.get('system', {}).get('parse_error_count', 0)}")
        except Exception as exc:
            status.config(text=f"read failed: {exc}")
        root.after(int(max(0.2, args.interval) * 1000), tick)

    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
