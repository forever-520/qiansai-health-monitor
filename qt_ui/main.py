import json
import math
import os
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import camera_client
import radar_iface
from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


BLUE = "#1769F4"
GREEN = "#13AD5B"
PINK = "#E83278"
VIOLET = "#7546C9"
ORANGE = "#F28B18"
RED = "#EC3346"
TEXT = "#101828"
MUTED = "#56637A"
LINE = "#E2E8F0"
BG = "#F7F9FC"
ASSET_DIR = Path(__file__).resolve().parent / "assets"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECENT_RECORD_MIN_INTERVAL = timedelta(minutes=5)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.care_logic import evaluate_care, neutralize_bed_text


RECENT_RECORD_BUCKET_SECONDS = 5 * 60


def gaussian(x: float, center: float, width: float) -> float:
    d = (x - center) / width
    return math.exp(-0.5 * d * d)


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")


class Pill(QFrame):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("OnlinePill")
        self.setFixedHeight(38)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)
        dot = QLabel()
        dot.setObjectName("GreenDot")
        dot.setFixedSize(10, 10)
        label = QLabel(text)
        label.setObjectName("OnlineText")
        layout.addWidget(dot)
        layout.addWidget(label)


class RoundIcon(QLabel):
    def __init__(self, text: str, color: str, bg: str, size: int = 64):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(size, size)
        self.setStyleSheet(
            f"""
            QLabel {{
                color: {color};
                background: {bg};
                border-radius: {size // 2}px;
                font-size: {max(22, size // 2 - 4)}px;
                font-weight: 900;
            }}
            """
        )


class MetricCard(Card):
    def __init__(
        self,
        icon: str,
        icon_color: str,
        icon_bg: str,
        title: str,
        value: str,
        unit: str,
        footer_left: str,
        footer_right: str = "",
        compact: bool = False,
    ):
        super().__init__()
        self._compact = compact
        self.setMinimumHeight(104 if compact else 132)
        self.setMaximumHeight(116 if compact else 142)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QGridLayout(self)
        layout.setContentsMargins(12 if compact else 18, 12 if compact else 18, 12 if compact else 18, 10 if compact else 14)
        layout.setHorizontalSpacing(10 if compact else 14)
        layout.setVerticalSpacing(6 if compact else 10)
        layout.addWidget(RoundIcon(icon, icon_color, icon_bg, 48 if compact else 64), 0, 0, 2, 1)

        title_label = QLabel(title)
        title_label.setObjectName("MetricTitle")
        value_row = QHBoxLayout()
        self.value_label = QLabel(value)
        self._icon_color = icon_color
        value_size = (20 if len(value) > 4 else 30) if compact else (24 if len(value) > 4 else 42)
        self.value_label.setStyleSheet(f"color: {icon_color}; font-size: {value_size}px; font-weight: 900;")
        unit_label = QLabel(unit)
        unit_label.setObjectName("Unit")
        value_row.addWidget(self.value_label)
        value_row.addWidget(unit_label, 0, Qt.AlignBottom)
        value_row.addStretch()

        stack = QVBoxLayout()
        stack.addWidget(title_label)
        stack.addLayout(value_row)
        layout.addLayout(stack, 0, 1)

        footer = QHBoxLayout()
        self.footer_left_label = QLabel(footer_left)
        self.footer_left_label.setObjectName("FooterText")
        footer.addWidget(self.footer_left_label)
        footer.addStretch()
        if footer_right:
            right = QLabel(footer_right)
            right.setObjectName("FooterText")
            footer.addWidget(right)
        layout.addLayout(footer, 1, 1)

    def set_value(self, text: str):
        self.value_label.setText(text)
        size = (20 if len(text) > 4 else 30) if self._compact else (24 if len(text) > 4 else 42)
        self.value_label.setStyleSheet(f"color: {self._icon_color}; font-size: {size}px; font-weight: 900;")

    def set_footer_left(self, text: str):
        self.footer_left_label.setText(text)


class DeviceStatusCard(Card):
    def __init__(
        self,
        icon: str,
        icon_color: str,
        icon_bg: str,
        title: str,
        status: str,
        detail_a: str,
        detail_b: str,
        compact: bool = False,
    ):
        super().__init__()
        self.setMinimumHeight(104 if compact else 124)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12 if compact else 18, 12 if compact else 16, 12 if compact else 18, 12 if compact else 16)
        layout.setSpacing(12 if compact else 18)
        layout.addWidget(RoundIcon(icon, icon_color, icon_bg, 48 if compact else 62), 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(7)
        title_label = QLabel(title)
        title_label.setObjectName("MetricTitle")
        status_label = QLabel(f"●  {status}")
        status_label.setStyleSheet(f"color: {icon_color}; font-size: 16px; font-weight: 800;")
        detail_one = QLabel(detail_a)
        detail_two = QLabel(detail_b)
        for label in (detail_one, detail_two):
            label.setObjectName("FooterText")
            label.setWordWrap(False)
        text.addWidget(title_label)
        text.addWidget(status_label)
        text.addWidget(detail_one)
        text.addWidget(detail_two)
        text.addStretch()
        layout.addLayout(text, 1)


class NavButton(QPushButton):
    def __init__(self, icon: str, text: str, compact: bool = False):
        super().__init__(icon if compact else f"{icon}  {text}")
        self.setCheckable(True)
        self.setObjectName("NavButton")
        self.setCursor(Qt.PointingHandCursor)
        if compact:
            self.setToolTip(text)


class WaveGraph(QWidget):
    def __init__(self, mode: str, compact: bool = False):
        super().__init__()
        self.mode = mode
        self.phase = 0.0
        self.wave_data = []
        self.max_points = 300
        self.zero_append_points = 12
        self.last_zero_append_at = 0.0
        self.setMinimumHeight(110 if compact else 168)

    def tick(self):
        self.phase += 2.5
        self.update()

    def set_wave(self, data: list):
        self.wave_data = list(data)[-self.max_points:]
        self.last_zero_append_at = 0.0
        self.update()

    def append_zero(self):
        if not self.wave_data:
            return
        now = time.monotonic()
        if now - self.last_zero_append_at < 2.0:
            return
        self.wave_data = (self.wave_data + [0] * self.zero_append_points)[-self.max_points:]
        self.last_zero_append_at = now
        self.update()

    def ecg_sample(self, x: float) -> float:
        beat_period = 92
        t = (x + self.phase * 2.15) / beat_period
        beat_index = math.floor(t)
        cycle = t - beat_index
        amplitude = 0.92 + 0.08 * math.sin(beat_index * 1.73)
        baseline = 0.035 * math.sin((x + self.phase) / 58) + 0.018 * math.sin((x + self.phase * 1.8) / 17)
        signal = (
            0.11 * gaussian(cycle, 0.15, 0.032)
            - 0.18 * gaussian(cycle, 0.305, 0.012)
            + 1.15 * gaussian(cycle, 0.335, 0.010)
            - 0.34 * gaussian(cycle, 0.365, 0.015)
            + 0.24 * gaussian(cycle, 0.58, 0.070)
        )
        noise = 0.018 * math.sin(x * 0.67 + self.phase * 0.08) + 0.010 * math.sin(x * 1.31)
        return signal * amplitude + baseline + noise

    def respiration_sample(self, x: float) -> float:
        w = max(1, self.width())
        t = (x + self.phase * 0.78) / 34
        envelope = 0.93 + 0.07 * math.sin((x + self.phase) / max(120, w * 0.42))
        rounded = 0.72 * math.sin(t) + 0.13 * math.sin(2 * t - 0.75) - 0.04 * math.sin(3 * t + 0.45)
        drift = 0.06 * math.sin((x + self.phase * 0.25) / 240)
        noise = 0.012 * math.sin(x * 0.39 + self.phase * 0.05)
        return rounded * envelope + drift + noise

    def _smooth_wave(self, data: list[float]) -> list[float]:
        if len(data) < 5:
            return list(data)

        smoothed = []
        for index, value in enumerate(data):
            left = max(0, index - 2)
            right = min(len(data), index + 3)
            window = data[left:right]
            avg = sum(window) / len(window)
            # 保留主要走势，但压一压尖锐毛刺。
            smoothed.append(value * 0.45 + avg * 0.55)
        return smoothed

    def _build_curve_path(self, points: list[tuple[float, float]]) -> QPainterPath:
        path = QPainterPath()
        if not points:
            return path
        path.moveTo(*points[0])
        if len(points) == 1:
            return path
        for index in range(1, len(points)):
            prev_x, prev_y = points[index - 1]
            x, y = points[index]
            ctrl1_x = prev_x + (x - prev_x) * 0.5
            ctrl1_y = prev_y
            ctrl2_x = prev_x + (x - prev_x) * 0.5
            ctrl2_y = y
            path.cubicTo(ctrl1_x, ctrl1_y, ctrl2_x, ctrl2_y, x, y)
        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))

        painter.setPen(QPen(QColor(47, 91, 145, 55), 1))
        for x in range(0, w + 1, 18):
            painter.drawLine(x, 0, x, h - 8)
        for y in range(0, h + 1, 18):
            painter.drawLine(0, y, w, y)

        painter.setPen(QPen(QColor(123, 147, 184, 185), 1))
        painter.drawLine(34, 0, 34, h - 8)
        painter.drawLine(34, h - 8, w - 8, h - 8)
        painter.setFont(QFont("Microsoft YaHei UI", 10))
        painter.setPen(QColor(212, 221, 235, 210))
        for i, label in enumerate(["2", "1", "0", "-1", "-2"]):
            y = 18 + i * ((h - 34) / 4)
            painter.drawText(6, int(y), label)

        if self.mode == "heart":
            color = QColor(86, 223, 145)
            scale = h * 0.44
        else:
            color = QColor(255, 101, 173)
            scale = h * 0.34

        points = []
        if self.wave_data:
            draw_w = max(35, w - 8) - 34
            data = self.wave_data[-draw_w:] if len(self.wave_data) >= draw_w else self.wave_data
            data = self._smooth_wave(data)
            step = draw_w / max(len(data) - 1, 1)
            for i, v in enumerate(data):
                x = 34 + i * step
                y = h / 2 - (v / 128.0) * scale
                points.append((x, y))
        else:
            points = [(x, h / 2) for x in range(34, max(35, w - 8), 2)]

        path = self._build_curve_path(points)
        glow = QPainterPath(path)

        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 60), 8))
        painter.drawPath(glow)
        painter.setPen(QPen(color, 2.2))
        painter.drawPath(path)


class WavePanel(Card):
    def __init__(self, compact: bool = False):
        super().__init__()
        self.setObjectName("WavePanel")
        self.setMinimumHeight(390 if compact else 540)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12 if compact else 18, 10 if compact else 16, 12 if compact else 18, 10 if compact else 16)
        layout.setSpacing(5 if compact else 8)

        self.heart = WaveGraph("heart", compact)
        self.breath = WaveGraph("breath", compact)
        self.heart_readout, heart_header = self._header("心率波形", "增益：20dB", "HR:  72 bpm", GREEN, compact)
        self.breath_readout, breath_header = self._header("呼吸波形", "增益：20dB", "RR:  16 rpm", PINK, compact)
        layout.addLayout(heart_header)
        layout.addWidget(self.heart)
        layout.addLayout(breath_header)
        layout.addWidget(self.breath)

    def _header(self, title: str, gain: str, readout: str, color: str, compact: bool = False):
        row = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {color}; font-size: {13 if compact else 16}px; font-weight: 900;")
        gain_label = QLabel(gain)
        gain_label.setStyleSheet(f"color: #C4CEDC; font-size: {11 if compact else 14}px; font-weight: 700;")
        readout_label = QLabel(readout)
        readout_label.setStyleSheet(f"color: {color}; font-size: {13 if compact else 17}px; font-weight: 900;")
        row.addWidget(title_label)
        row.addSpacing(18 if compact else 48)
        row.addWidget(gain_label)
        row.addStretch()
        row.addWidget(readout_label)
        return readout_label, row

    def tick(self):
        self.heart.tick()
        self.breath.tick()


class RuntimeCard(Card):
    def __init__(self, compact: bool = False):
        super().__init__()
        self._compact = compact
        self.setMinimumHeight(82 if compact else 118)
        self.setMaximumHeight(92 if compact else 124)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8 if compact else 18, 8 if compact else 12, 8 if compact else 18, 8 if compact else 12)
        layout.setSpacing(0)
        data = [
            ("▱", "有效帧", "0", "帧", ""),
            ("!", "解析错误", "0", "帧", ""),
            ("✓", "CRC错误", "0", "帧", ""),
            ("▤", "数据库队列", "0", "条", ""),
        ]
        stat_labels = []
        for index, item in enumerate(data):
            box, vl = self._stat(*item)
            stat_labels.append(vl)
            layout.addWidget(box)
            if index < len(data) - 1:
                line = QFrame()
                line.setFrameShape(QFrame.VLine)
                line.setObjectName("DashLine")
                layout.addWidget(line)
        self.frame_label = stat_labels[0]
        self.parse_err_label = stat_labels[1]
        self.crc_err_label = stat_labels[2]

    def _stat(self, icon: str, title: str, value: str, unit: str, sub: str):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(6 if self._compact else 12, 0, 6 if self._compact else 12, 0)
        row.setSpacing(8 if self._compact else 16)
        icon_label = QLabel(icon)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet(f"color: {BLUE}; font-size: {22 if self._compact else 34}px; font-weight: 500;")
        row.addWidget(icon_label)
        col = QVBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("StatTitle")
        value_row = QHBoxLayout()
        value_label = QLabel(value)
        value_label.setStyleSheet(f"color: #2D75DE; font-size: {20 if self._compact else 28}px; font-weight: 500;")
        unit_label = QLabel(unit)
        unit_label.setObjectName("Unit")
        value_row.addWidget(value_label)
        value_row.addWidget(unit_label, 0, Qt.AlignBottom)
        value_row.addStretch()
        sub_label = QLabel(sub)
        sub_label.setObjectName("SmallText")
        col.addWidget(title_label)
        col.addLayout(value_row)
        col.addWidget(sub_label)
        row.addLayout(col)
        return box, value_label


class MonitorStatusBar(Card):
    def __init__(self, compact: bool = False):
        super().__init__()
        height = 78 if compact else 96
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16 if compact else 18, 10, 16 if compact else 18, 10)
        layout.setSpacing(16 if compact else 22)

        self.alarm_label = self._block("告警", "●  无告警", GREEN)
        self.motion_label = self._block("体动 / 链路", "静止  ·  雷达正常", BLUE)
        self.frame_label = self._block("有效帧", "0", BLUE)
        self.parse_err_label = self._block("解析错误", "0", ORANGE)
        self.crc_err_label = self._block("CRC错误", "0", RED)

        for widget, stretch in [
            (self.alarm_label["box"], 2),
            (self.motion_label["box"], 3),
            (self.frame_label["box"], 1),
            (self.parse_err_label["box"], 1),
            (self.crc_err_label["box"], 1),
        ]:
            layout.addWidget(widget, stretch)

    def _block(self, title: str, value: str, color: str):
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("StatTitle")
        value_label = QLabel(value)
        value_label.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: 900;")
        value_label.setWordWrap(True)
        col.addWidget(title_label)
        col.addWidget(value_label)
        return {"box": box, "value": value_label}


class SerialDataPanel(Card):
    def __init__(self):
        super().__init__()
        self.labels = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(7)
        title = QLabel("串口解析字段总览")
        title.setObjectName("SideTitle")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        groups = [
            ("人体", [("exist", "存在"), ("motion_state", "运动"), ("motion_val", "体动"), ("distance", "距离"), ("position", "方位")]),
            ("生命体征", [("heart_rate", "心率"), ("breath_rate", "呼吸"), ("breath_state", "呼吸状态"), ("heart_wave_len", "心率波形"), ("breath_wave_len", "呼吸波形")]),
            ("睡眠", [("bed", "监测码"), ("sleep_state", "状态"), ("score", "评分"), ("sleep_time", "总时长"), ("turn_apnea", "翻身/暂停")]),
            ("系统", [("frames", "有效帧"), ("errors", "解析/CRC"), ("rating", "评级"), ("exception", "异常"), ("last_update", "更新")]),
        ]
        for group_index, (group_title, fields) in enumerate(groups):
            base_col = (group_index % 2) * 2
            base_row = (group_index // 2) * 7
            header = QLabel(group_title)
            header.setObjectName("FieldGroupTitle")
            grid.addWidget(header, base_row, base_col, 1, 2)
            for offset, (key, label_text) in enumerate(fields, start=1):
                name = QLabel(label_text)
                name.setObjectName("SmallText")
                value = QLabel("-")
                value.setObjectName("DenseValue")
                value.setWordWrap(False)
                grid.addWidget(name, base_row + offset, base_col)
                grid.addWidget(value, base_row + offset, base_col + 1)
                self.labels[key] = value
        layout.addLayout(grid, 1)

    def set_values(self, values: dict):
        for key, value in values.items():
            if key in self.labels:
                self.labels[key].setText(str(value))


class BedsideImagePreview(QLabel):
    def __init__(self, image_path: Path):
        super().__init__()
        self._image_path = Path(image_path)
        self._pixmap = QPixmap(str(self._image_path))
        self.setObjectName("BedsideImage")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(520, 300)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setText("等待影像")

    def sizeHint(self):
        return QSize(760, 430)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_pixmap()

    def set_image(self, image_path: Path):
        self._image_path = Path(image_path)
        self._pixmap = QPixmap(str(self._image_path))
        self._refresh_pixmap()

    def _refresh_pixmap(self):
        if self._pixmap.isNull():
            self.setText("等待影像")
            return
        # 保持原图比例，避免大图的原始尺寸把右侧护理记录栏挤出屏幕。
        scaled = self._pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setText("")
        self.setPixmap(scaled)


class NursingRecordPanel(Card):
    def __init__(self):
        super().__init__()
        self.values = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        title = QLabel("护理记录")
        title.setObjectName("CareTitle")
        layout.addWidget(title)
        for key, icon, label, value, color in [
            ("capture_time", "◷", "采集时间", "--", TEXT),
            ("result", "☑", "判断结果", "等待数据", GREEN),
            ("radar", "⌁", "雷达状态", "等待数据", GREEN),
            ("note", "▤", "护理备注", "等待画面", GREEN),
        ]:
            layout.addWidget(self._row(key, icon, label, value, color))
        layout.addStretch()

    def _row(self, key: str, icon: str, label_text: str, value_text: str, color: str):
        frame = QFrame()
        frame.setObjectName("CareRow")
        frame.setMinimumHeight(26)
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 1, 0, 1)
        row.setSpacing(6)
        icon_label = QLabel(icon)
        icon_label.setObjectName("CareIcon")
        icon_label.setFixedWidth(18)
        label = QLabel(label_text)
        label.setObjectName("CareLabel")
        label.setMinimumWidth(68)
        value = QLabel(value_text)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        value.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: 900;")
        value.setWordWrap(False)
        value.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        row.addWidget(icon_label)
        row.addWidget(label)
        row.addWidget(value, 1)
        self.values[key] = value
        return frame

    def set_values(self, capture_time: str, result: str, radar: str, note: str):
        self.values["capture_time"].setText(capture_time)
        self.values["result"].setText(result)
        self.values["radar"].setText(radar)
        self.values["note"].setText(note)


class RecentRecordPanel(Card):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.on_more = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("近期记录")
        title.setObjectName("CareTitle")
        more = QPushButton("查看更多 ›")
        more.setObjectName("MoreButton")
        more.setCursor(Qt.PointingHandCursor)
        more.clicked.connect(self._show_more)
        head.addWidget(title)
        head.addStretch()
        head.addWidget(more)
        layout.addLayout(head)
        for _ in range(3):
            row = QHBoxLayout()
            row.setSpacing(9)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {GREEN}; font-size: 13px;")
            time_label = QLabel("--:--:--")
            time_label.setObjectName("CareLabel")
            value = QLabel("体征平稳")
            value.setObjectName("CareLabel")
            value.setWordWrap(True)
            badge = QLabel("画面清晰")
            badge.setObjectName("ImageBadge")
            row.addWidget(dot)
            row.addWidget(time_label)
            row.addStretch()
            row.addWidget(value, 1)
            row.addWidget(badge)
            layout.addLayout(row)
            self.rows.append((time_label, value, badge))

    def set_records(self, records: list[tuple[str, str, str]]):
        for index, labels in enumerate(self.rows):
            time_label, value, badge = labels
            record = records[index] if index < len(records) else None
            if not record:
                time_label.setText("--:--:--")
                value.setText("暂无记录")
                badge.setText("等待")
                continue
            tm, text, status = record
            time_label.setText(tm)
            value.setText(text)
            badge.setText(status)

    def _show_more(self):
        if self.on_more:
            self.on_more()


class RecentRecordsDialog(QDialog):
    def __init__(self, records: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("近 7 天护理记录")
        self.resize(520, 460)
        self.setObjectName("RecordsDialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("近 7 天护理记录")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("RecordsScroll")
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)

        if records:
            for record in records:
                body_layout.addWidget(self._record_row(record))
        else:
            empty = QLabel("最近 7 天暂无护理记录")
            empty.setObjectName("CareLabel")
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(180)
            body_layout.addWidget(empty)
        body_layout.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        close = QPushButton("关闭")
        close.setObjectName("SaveButton")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def _record_row(self, record: dict) -> QFrame:
        row = QFrame()
        row.setObjectName("RecordListRow")
        row_layout = QGridLayout(row)
        row_layout.setContentsMargins(12, 10, 12, 10)
        row_layout.setHorizontalSpacing(10)
        row_layout.setVerticalSpacing(4)

        when = QLabel(record.get("display_time", "--"))
        when.setObjectName("RecordTime")
        text = QLabel(neutralize_bed_text(record.get("text", "护理记录"), "护理记录"))
        text.setObjectName("RecordText")
        text.setWordWrap(True)
        badge = QLabel(record.get("badge", "已记录"))
        badge.setObjectName("ImageBadge")
        note = QLabel(neutralize_bed_text(record.get("note", ""), ""))
        note.setObjectName("CareLabel")
        note.setWordWrap(True)

        row_layout.addWidget(when, 0, 0)
        row_layout.addWidget(text, 0, 1)
        row_layout.addWidget(badge, 0, 2)
        row_layout.addWidget(note, 1, 1, 1, 2)
        row_layout.setColumnStretch(1, 1)
        return row


class SideCard(Card):
    def __init__(self, title: str, icon: str = ""):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 14, 16, 14)
        self.layout.setSpacing(8)
        header = QLabel(f"{icon}  {title}".strip())
        header.setObjectName("SideTitle")
        self.layout.addWidget(header)

    def add_row(self, text: str, value: str = "", color: str = MUTED):
        row = QHBoxLayout()
        label = QLabel(text)
        label.setObjectName("SideRow")
        row.addWidget(label)
        row.addStretch()
        if value:
            value_label = QLabel(value)
            value_label.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: 700;")
            row.addWidget(value_label)
        self.layout.addLayout(row)

    def add_bullet(self, text: str):
        label = QLabel(f"•  {text}")
        label.setObjectName("Bullet")
        label.setWordWrap(True)
        self.layout.addWidget(label)


class LineChart(QWidget):
    def __init__(self, values, color: str, min_value: int, max_value: int):
        super().__init__()
        self.values = values
        self.color = QColor(color)
        self.min_value = min_value
        self.max_value = max_value
        self.setMinimumHeight(136)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pad_left, pad_right, pad_top, pad_bottom = 44, 20, 16, 30
        chart_w = w - pad_left - pad_right
        chart_h = h - pad_top - pad_bottom
        painter.setPen(QPen(QColor("#E4EAF2"), 1))
        for n in [0, 0.5, 1]:
            y = pad_top + chart_h * n
            painter.drawLine(pad_left, int(y), w - pad_right, int(y))
        painter.setPen(QPen(QColor("#D4DCE8"), 1))
        painter.drawLine(pad_left, pad_top, pad_left, h - pad_bottom)
        painter.drawLine(pad_left, h - pad_bottom, w - pad_right, h - pad_bottom)

        points = []
        for index, value in enumerate(self.values):
            x = pad_left + chart_w * index / max(1, len(self.values) - 1)
            ratio = (value - self.min_value) / (self.max_value - self.min_value)
            y = pad_top + chart_h - ratio * chart_h
            points.append((x, y, value))

        path = QPainterPath()
        for i, (x, y, _) in enumerate(points):
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)

        fill = QPainterPath(path)
        fill.lineTo(points[-1][0], h - pad_bottom)
        fill.lineTo(points[0][0], h - pad_bottom)
        fill.closeSubpath()
        gradient = QLinearGradient(0, pad_top, 0, h - pad_bottom)
        gradient.setColorAt(0, QColor(self.color.red(), self.color.green(), self.color.blue(), 45))
        gradient.setColorAt(1, QColor(self.color.red(), self.color.green(), self.color.blue(), 0))
        painter.fillPath(fill, gradient)

        painter.setPen(QPen(self.color, 2.2))
        painter.drawPath(path)
        painter.setBrush(self.color)
        painter.setPen(Qt.NoPen)
        for x, y, _ in points:
            painter.drawEllipse(QPoint(int(x), int(y)), 4, 4)

        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.setPen(QColor("#536176"))
        today = datetime.now()
        labels = [(today - timedelta(days=6 - i)).strftime("%m-%d") for i in range(7)]
        for i, (x, y, value) in enumerate(points):
            painter.drawText(int(x - 16), h - 8, labels[i])
            painter.drawText(int(x - 7), int(y - 12), str(value))
        for index, value in enumerate([self.max_value, round((self.max_value + self.min_value) / 2), self.min_value]):
            painter.drawText(8, int(pad_top + index * chart_h / 2 + 4), str(value))


class SleepMotion(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(84)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        painter.setPen(QPen(QColor("#DFE6EF"), 1, Qt.DashLine))
        for n in [0.25, 0.5, 0.75]:
            painter.drawLine(0, int(h * n), w, int(h * n))
        painter.setPen(QPen(QColor("#5B95FF"), 1.3))
        path = QPainterPath()
        for x in range(0, w, 3):
            noise = abs(math.sin(x * 0.19) * math.sin(x * 0.047))
            burst = 34 if (w * 0.14 < x < w * 0.28) or (w * 0.72 < x < w * 0.79) else 10
            y = h - 12 - noise * burst - abs(math.sin(x * 0.91)) * 6
            path.moveTo(x, y) if x == 0 else path.lineTo(x, y)
        painter.drawPath(path)


class BarSpark(QWidget):
    def __init__(self, color: str, values):
        super().__init__()
        self.color = QColor(color)
        self.values = values
        self.setMinimumHeight(44)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        max_value = max(self.values)
        step = w / len(self.values)
        painter.setBrush(self.color)
        painter.setPen(Qt.NoPen)
        for index, value in enumerate(self.values):
            bar_h = (value / max_value) * (h - 5)
            painter.drawRoundedRect(int(index * step), int(h - bar_h), max(2, int(step - 3)), int(bar_h), 1, 1)


class QualityWave(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        painter.setPen(QPen(QColor("#2AB56B"), 2))
        path = QPainterPath()
        for x in range(0, w, 3):
            y = h / 2 + math.sin(x / 9) * 8 + math.sin(x / 23) * 5
            path.moveTo(x, y) if x == 0 else path.lineTo(x, y)
        painter.drawPath(path)


class HealthMonitorWindow(QMainWindow):
    capture_status_signal = Signal(str)
    capture_preview_signal = Signal(str)

    def __init__(self):
        super().__init__()
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        force_compact = os.environ.get("RADAR_COMPACT", "").strip() == "1"
        self.compact = force_compact or bool(available and (available.width() < 1000 or available.height() < 720))
        self.setWindowTitle("床旁非接触生命体征监测")
        if self.compact and available:
            self.resize(available.width(), available.height())
            self.setMinimumSize(640, 480)
        else:
            self.resize(1280, 720)
            self.setMinimumSize(1120, 680)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.drag_pos = None

        self.stack = QStackedWidget()
        self.nav_buttons = []
        self.wave_panel = WavePanel(self.compact)
        self.runtime_card = RuntimeCard(self.compact)
        self.capture_timer = QTimer(self)
        self.capture_timer.timeout.connect(self._capture_once)
        self.capture_inflight = False
        self.capture_host = os.environ.get("RK3576_SLAVE_CAPTURE_HOST", "127.0.0.1").strip() or "127.0.0.1"
        self.capture_port = int(os.environ.get("RK3576_SLAVE_CAPTURE_PORT", "8000"))
        self.capture_output_path = ASSET_DIR / "bedside_live_capture.jpg"
        self.capture_save_dir = ASSET_DIR / "captures"
        self.capture_status_text = None
        self.capture_preview = None
        self.title_clock_label = None
        self.nursing_panel = None
        self.recent_record_panel = None
        self.image_status_card = None
        self.image_bed_card = None
        self.image_safety_card = None
        self.last_vitals_snapshot = None
        self.records_path = ASSET_DIR / "recent_records.json"
        self.recent_records = self._load_recent_records()
        latest_record = self.recent_records[0] if self.recent_records else None
        self.last_recent_signature = latest_record.get("signature") if latest_record else None
        self.last_recent_record_at = self._parse_record_time(latest_record) if latest_record else None
        self.last_recent_bucket = latest_record.get("bucket") if latest_record else None
        self.capture_status_signal.connect(self._set_capture_status)
        self.capture_preview_signal.connect(self._refresh_capture_preview)

        root = QWidget()
        root.setObjectName("Root")
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._sidebar())
        shell.addWidget(self._workspace(), 1)
        self.setCentralWidget(root)
        self._apply_style()
        self._activate(0)

        wave_timer = QTimer(self)
        wave_timer.timeout.connect(self.wave_panel.tick)
        wave_timer.start(32)

        threading.Thread(target=radar_iface.start_safe, daemon=True).start()

        data_timer = QTimer(self)
        data_timer.timeout.connect(self._refresh_data)
        data_timer.start(250)

    def _refresh_data(self):
        err = radar_iface.get_start_error()
        if err:
            self.card_heart.set_footer_left(f"●  串口错误: {err[:20]}")
            if hasattr(self, "monitor_status"):
                self.monitor_status.alarm_label["value"].setText(f"串口错误: {err[:22]}")
            return

        v = radar_iface.get_vitals()
        self.last_vitals_snapshot = v
        self._refresh_dynamic_time()

        self.card_heart.set_value(str(v.heart_rate))
        self.card_heart.set_footer_left("●  正常" if 60 <= v.heart_rate <= 100 else "●  异常")

        self.card_breath.set_value(str(v.breath_rate))
        self.card_breath.set_footer_left(
            f"●  {radar_iface.BREATH_STATE_TEXT.get(v.breath_state, '正常')}"
            if v.breath_state else "●  正常"
        )

        motion_text = "静止" if v.motion_val < 20 else ("轻微" if v.motion_val < 60 else "活跃")
        if hasattr(self, "card_motion"):
            self.card_motion.set_value(str(v.motion_val))
            self.card_motion.set_footer_left(f"●  {motion_text}")

        self.wave_panel.heart_readout.setText(f"HR:  {v.heart_rate} bpm")
        self.wave_panel.breath_readout.setText(f"RR:  {v.breath_rate} rpm")

        if v.heart_rate > 0 and v.heart_wave:
            self.wave_panel.heart.set_wave(v.heart_wave)
        else:
            self.wave_panel.heart.append_zero()
        if v.breath_rate > 0 and v.breath_wave:
            self.wave_panel.breath.set_wave(v.breath_wave)
        else:
            self.wave_panel.breath.append_zero()

        if hasattr(self, "runtime_card"):
            self.runtime_card.frame_label.setText(str(v.frame_count))
            self.runtime_card.parse_err_label.setText(str(v.parse_error_count))
            self.runtime_card.crc_err_label.setText(str(v.checksum_error_count))
        if hasattr(self, "monitor_status"):
            if not v.heart_rate and not v.breath_rate:
                alarm = "●  等待数据"
            else:
                alarm = "●  无告警" if 60 <= v.heart_rate <= 100 and v.breath_state in (0, 1) else "●  请关注生命体征"
            self.monitor_status.alarm_label["value"].setText(alarm)
            self.monitor_status.motion_label["value"].setText(f"{motion_text} {v.motion_val}/100  ·  雷达正常")
            self.monitor_status.frame_label["value"].setText(str(v.frame_count))
            self.monitor_status.parse_err_label["value"].setText(str(v.parse_error_count))
            self.monitor_status.crc_err_label["value"].setText(str(v.checksum_error_count))
        if hasattr(self, "serial_panel"):
            self.serial_panel.set_values(
                {
                    "exist": radar_iface.EXIST_TEXT.get(v.exist, "未知"),
                    "motion_state": radar_iface.MOTION_TEXT.get(v.motion_state, "未知"),
                    "motion_val": f"{v.motion_val}/100",
                    "distance": f"{v.distance} cm",
                    "position": f"X{v.x} Y{v.y} Z{v.z}",
                    "heart_rate": f"{v.heart_rate} bpm",
                    "breath_rate": f"{v.breath_rate} rpm",
                    "breath_state": radar_iface.BREATH_STATE_TEXT.get(v.breath_state, "正常" if v.breath_state == 0 else "未知"),
                    "heart_wave_len": f"{len(v.heart_wave)} 点",
                    "breath_wave_len": f"{len(v.breath_wave)} 点",
                    "bed": str(v.bed),
                    "sleep_state": radar_iface.SLEEP_STATE_TEXT.get(v.sleep_state, "未知"),
                    "score": f"{v.score}%",
                    "sleep_time": f"{v.total_sleep} min",
                    "awake_light_deep": f"{v.awake_time}/{v.light_time}/{v.deep_time} min",
                    "avg_vitals": f"{v.avg_breath} rpm / {v.avg_heart} bpm",
                    "turn_apnea": f"{v.turn_over} / {v.apnea}",
                    "motion_ratio": f"{v.big_motion_ratio}% / {v.small_motion_ratio}%",
                    "out_bed": f"{v.out_bed_time} / {v.out_bed_count} 次",
                    "exception": radar_iface.SLEEP_EXCEPTION_TEXT.get(v.exception, "未知"),
                    "rating": radar_iface.SLEEP_RATING_TEXT.get(v.rating, "未知"),
                    "struggle": radar_iface.STRUGGLE_TEXT.get(v.struggle, "未知"),
                    "nobody_timer": radar_iface.NOBODY_TIMER_TEXT.get(v.nobody_timer, "未知"),
                    "frames": v.frame_count,
                    "errors": f"{v.parse_error_count} / {v.checksum_error_count}",
                    "last_update": "1s",
                }
            )
        if hasattr(self, "last_hex_label"):
            self.last_hex_label.setText(v.last_frame_hex or "-")
        self._refresh_care_records(v)

    def _refresh_dynamic_time(self):
        if self.title_clock_label is not None:
            self.title_clock_label.setText(f"▣  {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}")

    def _record_cutoff(self, days: int) -> datetime:
        return datetime.now() - timedelta(days=days)

    def _parse_record_time(self, record: dict) -> datetime | None:
        try:
            return datetime.fromisoformat(record.get("iso_time", ""))
        except (TypeError, ValueError):
            return None

    def _records_within_days(self, days: int, records: list[dict] | None = None) -> list[dict]:
        cutoff = self._record_cutoff(days)
        source_records = self.recent_records if records is None else records
        records = [
            record for record in sorted(source_records, key=lambda item: item.get("iso_time", ""), reverse=True)
            if (self._parse_record_time(record) or datetime.min) >= cutoff
        ]
        spaced = []
        for record in records:
            record_time = self._parse_record_time(record)
            if record_time is None:
                continue
            if any(abs((self._parse_record_time(saved) or datetime.min) - record_time) < RECENT_RECORD_MIN_INTERVAL for saved in spaced):
                continue
            spaced.append(record)
        return spaced

    def _load_recent_records(self) -> list[dict]:
        try:
            if not self.records_path.exists():
                return []
            data = json.loads(self.records_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        records = [item for item in data if isinstance(item, dict)]
        return self._records_within_days(7, records)[:80]

    def _save_recent_records(self):
        try:
            self.records_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.records_path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(self._records_within_days(7)[:80], ensure_ascii=False, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            tmp_path.replace(self.records_path)
        except OSError:
            pass

    def _recent_panel_records(self) -> list[tuple[str, str, str]]:
        records = []
        for record in self._records_within_days(7)[:3]:
            records.append((
                record.get("time", "--:--:--"),
                neutralize_bed_text(record.get("text", "护理记录"), "护理记录"),
                record.get("badge", "已记录"),
            ))
        return records

    def _show_recent_records(self):
        dialog = RecentRecordsDialog(self._records_within_days(7), self)
        dialog.exec()

    def _refresh_care_records(self, v):
        now = datetime.now()
        care = evaluate_care(
            exist=v.exist,
            bed=v.bed,
            sleep_state=v.sleep_state,
            motion_val=v.motion_val,
            heart_rate=v.heart_rate,
            breath_rate=v.breath_rate,
            frame_count=v.frame_count,
            online=True,
        )
        capture_time = now.strftime("%m-%d  %H:%M:%S")

        if self.nursing_panel is not None:
            self.nursing_panel.set_values(
                capture_time,
                neutralize_bed_text(care.result, "等待实时数据"),
                neutralize_bed_text(care.radar, "等待影像复核"),
                neutralize_bed_text(care.note, "等待生命体征"),
            )
        if self.recent_record_panel is not None:
            can_record_by_time = self.last_recent_record_at is None or now - self.last_recent_record_at >= RECENT_RECORD_MIN_INTERVAL
            bucket = int(now.timestamp() // RECENT_RECORD_BUCKET_SECONDS)
            record_signature = f"{care.signature}|{bucket}"
            if care.recordable and can_record_by_time and record_signature != self.last_recent_signature:
                self.recent_records.insert(
                    0,
                    {
                        "iso_time": now.isoformat(timespec="seconds"),
                        "display_time": now.strftime("%m-%d %H:%M:%S"),
                        "time": now.strftime("%H:%M:%S"),
                        "text": neutralize_bed_text(care.recent_event, "护理记录"),
                        "badge": "已记录",
                        "note": neutralize_bed_text(care.note, ""),
                        "signature": record_signature,
                        "bucket": bucket,
                    },
                )
                self.recent_records = self._records_within_days(7)[:80]
                self.last_recent_signature = record_signature
                self.last_recent_record_at = now
                self.last_recent_bucket = bucket
                self._save_recent_records()
            self.recent_record_panel.set_records(self._recent_panel_records())
        if self.image_status_card is not None:
            self.image_status_card.set_value("观察中" if self.capture_timer.isActive() else "待观察")
            self.image_status_card.set_footer_left(f"●  最新 {now.strftime('%H:%M:%S')}")
        if self.image_bed_card is not None:
            self.image_bed_card.set_value("清晰" if v.frame_count else "待复核")
            self.image_bed_card.set_footer_left("●  完整显示")
        if self.image_safety_card is not None:
            self.image_safety_card.set_value(care.safety)
            self.image_safety_card.set_footer_left(f"●  {care.note}")

    def _set_capture_status(self, text: str):
        if self.capture_status_text is not None:
            self.capture_status_text.setText(text)

    def _refresh_capture_preview(self, image_path: str | Path | None = None):
        if image_path is None:
            image_path = self.capture_output_path
        if self.capture_preview is not None and Path(image_path).exists():
            self.capture_preview.set_image(Path(image_path))

    def _capture_once(self):
        if self.capture_inflight:
            return
        self.capture_inflight = True
        self.capture_status_signal.emit("抓拍中…")

        def worker():
            try:
                saved = camera_client.request_image(
                    host=self.capture_host,
                    port=self.capture_port,
                    output_path=self.capture_output_path,
                    trigger=0x01,
                    count=1,
                    timeout=60,
                )
            except Exception as exc:
                self.capture_inflight = False
                self.capture_status_signal.emit(f"抓拍失败：{str(exc)[:24]}")
                return
            self.capture_inflight = False
            self.capture_preview_signal.emit(str(saved))
            self.capture_status_signal.emit("最新影像已更新")

        threading.Thread(target=worker, daemon=True).start()

    def _start_observe(self):
        if not self.capture_timer.isActive():
            self.capture_timer.start(1500)
        self._set_capture_status("持续观察中…")
        self._capture_once()

    def _stop_observe(self):
        self.capture_timer.stop()
        self._set_capture_status("观察已暂停")

    def _save_capture(self):
        if not self.capture_output_path.exists():
            self._set_capture_status("当前无可保存影像")
            return
        self.capture_save_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.capture_save_dir / f"bedside_{stamp}.jpg"
        shutil.copy2(self.capture_output_path, target)
        self._set_capture_status(f"已保存：{target.name}")

    def _sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(142)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        logo_area = QFrame()
        logo_area.setFixedHeight(70)
        logo_layout = QVBoxLayout(logo_area)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo = QLabel()
        logo.setObjectName("Logo")
        logo.setAlignment(Qt.AlignCenter)
        logo_pixmap = QPixmap(str(ASSET_DIR / "health_logo.png"))
        if not logo_pixmap.isNull():
            logo.setPixmap(logo_pixmap.scaled(74, 74, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo_layout.addWidget(logo, 0, Qt.AlignCenter)
        layout.addWidget(logo_area)

        nav = QVBoxLayout()
        nav.setContentsMargins(0, 32, 0, 0)
        nav.setSpacing(8)
        for index, (icon, text) in enumerate([("▱", "实时监测"), ("◷", "历史摘要"), ("▣", "床旁影像")]):
            button = NavButton(icon, text)
            button.clicked.connect(lambda _, i=index: self._activate(i))
            self.nav_buttons.append(button)
            nav.addWidget(button)
        nav.addStretch()
        layout.addLayout(nav, 1)

        collapse = QLabel("≪  收起")
        collapse.setObjectName("Collapse")
        collapse.setAlignment(Qt.AlignCenter)
        collapse.setFixedHeight(70)
        layout.addWidget(collapse)
        return sidebar

    def _workspace(self):
        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._titlebar())
        self.stack.addWidget(self._wrap_page(self._realtime_page()))
        self.stack.addWidget(self._wrap_page(self._history_page()))
        self.stack.addWidget(self._wrap_page(self._bedside_image_page()))
        layout.addWidget(self.stack, 1)
        return workspace

    def _wrap_page(self, page: QWidget):
        return page

    def _titlebar(self):
        titlebar = QFrame()
        titlebar.setObjectName("Titlebar")
        titlebar.setFixedHeight(54 if self.compact else 70)
        row = QHBoxLayout(titlebar)
        row.setContentsMargins(14 if self.compact else 24, 0, 10 if self.compact else 18, 0)
        row.setSpacing(8 if self.compact else 16)
        title = QLabel("生命体征监测" if self.compact else "床旁非接触生命体征监测")
        title.setObjectName("WindowTitle")
        row.addWidget(title)
        row.addStretch()
        if not self.compact:
            self.title_clock_label = QLabel("▣  --")
            row.addWidget(self.title_clock_label)
            row.addWidget(Pill("雷达在线"))
        for text, slot in [("－", self.showMinimized), ("□", self._toggle_max), ("×", self.close)]:
            button = QPushButton(text)
            button.setObjectName("WindowButton")
            button.clicked.connect(slot)
            row.addWidget(button)
        return titlebar

    def _realtime_page(self):
        if self.compact:
            return self._realtime_page_compact()

        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(10 if self.compact else 18, 10 if self.compact else 18, 10 if self.compact else 18, 10 if self.compact else 18)
        layout.setHorizontalSpacing(10 if self.compact else 16)
        layout.setVerticalSpacing(10 if self.compact else 14)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 0 if not self.compact else 1)
        layout.setRowStretch(0, 0)
        layout.setRowStretch(1, 1)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(10 if self.compact else 14)
        metrics.setVerticalSpacing(10 if self.compact else 0)
        self.card_heart = MetricCard("♥", GREEN, "#E9F9F0", "心率", "0", "bpm", "●  等待数据", "参考范围 60~100", compact=self.compact)
        self.card_breath = MetricCard("肺", PINK, "#FFF0F6", "呼吸", "0", "rpm", "●  等待数据", "参考范围 12~20", compact=self.compact)
        self.card_bed = MetricCard("眠", BLUE, "#EDF4FF", "", "睡眠阶段", "", "", "", compact=self.compact)
        self.card_motion = MetricCard("行", VIOLET, "#F3EFFD", "体动强度", "0", "/100", "●  静止", "基线 20", compact=self.compact)
        top_cards = [self.card_heart, self.card_breath, self.card_bed, self.card_motion]
        for index, card in enumerate(top_cards):
            card.setFixedHeight(104 if self.compact else 132)
            if self.compact:
                metrics.addWidget(card, index // 2, index % 2)
            else:
                metrics.addWidget(card, 0, index)
        layout.addLayout(metrics, 0, 0)
        layout.addWidget(self.wave_panel, 1, 0)

        side = QVBoxLayout()
        side.setSpacing(14)
        alarm = SideCard("告警原因", "🚨")
        for text, value in [
            ("●  呼吸率过低", "0"),
            ("●  呼吸率过高", "0"),
            ("●  心率过低", "0"),
            ("●  心率过高", "0"),
            ("●  体动异常增强", "1"),
            ("●  信号质量差", "0"),
            ("●  设备离线", "0"),
        ]:
            alarm.add_row(text, value)
        alarm.add_row("查看全部  ›", "", BLUE)
        side.addWidget(alarm)
        advice = SideCard("当前建议", "▣")
        for text in ["生命体征正常，建议继续观察", "保持患者处于监测区域中心", "避免遮挡，确保视线通畅", "如需移动，请先暂停监测"]:
            advice.add_bullet(text)
        side.addWidget(advice)
        link = SideCard("链路状态", "🔗")
        for text in ["雷达", "数据采集", "数据传输", "数据存储"]:
            link.add_row(f"●  {text}", "正常", GREEN)
        side.addWidget(link)
        side.addStretch()
        side_box = QWidget()
        if not self.compact:
            side_box.setFixedWidth(210)
        side_box.setLayout(side)
        if self.compact:
            layout.addWidget(side_box, 3, 0)
        else:
            layout.addWidget(side_box, 0, 1, 3, 1)
        return page

    def _realtime_page_compact(self):
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)
        layout.setRowStretch(0, 0)
        layout.setRowStretch(1, 1)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        self.card_heart = MetricCard("♥", GREEN, "#E9F9F0", "心率", "0", "bpm", "●  等待数据", "60~100", compact=True)
        self.card_breath = MetricCard("肺", PINK, "#FFF0F6", "呼吸", "0", "rpm", "●  等待数据", "12~20", compact=True)
        self.card_bed = MetricCard("眠", BLUE, "#EDF4FF", "", "睡眠阶段", "", "", "", compact=True)
        for column, card in enumerate([self.card_heart, self.card_breath, self.card_bed]):
            card.setFixedHeight(104)
            metrics.addWidget(card, 0, column)
            metrics.setColumnStretch(column, 1)

        layout.addLayout(metrics, 0, 0)
        layout.addWidget(self.wave_panel, 1, 0)
        return page

    def _history_page(self):
        if self.compact:
            return self._history_page_compact()

        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(10 if self.compact else 18, 10 if self.compact else 18, 10 if self.compact else 18, 10 if self.compact else 18)
        layout.setHorizontalSpacing(10 if self.compact else 18)
        layout.setVerticalSpacing(10 if self.compact else 18)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 0 if not self.compact else 1)

        top = QGridLayout()
        top.setHorizontalSpacing(10 if self.compact else 14)
        top.setVerticalSpacing(10 if self.compact else 0)
        history_cards = [
            MetricCard("♥", GREEN, "#E9F9F0", "昨夜平均心率", "68", "bpm", "较前一晚  ↓  3 bpm", compact=self.compact),
            MetricCard("肺", PINK, "#FFF0F6", "昨夜平均呼吸", "15", "rpm", "较前一晚  ↓  1 rpm", compact=self.compact),
            MetricCard("时", BLUE, "#EDF4FF", "有效监测时长", "8小时 32分", "", "●  较前一晚  +22 分钟", compact=self.compact),
            MetricCard("!", VIOLET, "#F3EFFD", "异常事件", "2", "次", "较前一晚  ↓  3 次", compact=self.compact),
        ]
        for index, card in enumerate(history_cards):
            if self.compact:
                top.addWidget(card, index // 2, index % 2)
            else:
                top.addWidget(card, 0, index)
        layout.addLayout(top, 0, 0, 1, 1 if self.compact else 2)

        trend = Card()
        trend_layout = QVBoxLayout(trend)
        trend_layout.setContentsMargins(20, 16, 20, 16)
        head = QHBoxLayout()
        head.addWidget(QLabel("7天趋势"))
        head.addStretch()
        tab = QLabel("  7天    14天  ")
        tab.setObjectName("TabLabel")
        head.addWidget(tab)
        trend_layout.addLayout(head)
        legend = QLabel("━  心率 (bpm)      ━  呼吸 (rpm)")
        legend.setStyleSheet(f"color: {BLUE}; font-weight: 700;")
        trend_layout.addWidget(legend)
        trend_layout.addWidget(LineChart([72, 70, 68, 69, 67, 68, 68], BLUE, 40, 100))
        trend_layout.addWidget(LineChart([16, 15, 16, 15, 14, 15, 15], "#FF2F7D", 0, 30))
        layout.addWidget(trend, 1, 0)

        sleep = Card()
        sleep_layout = QVBoxLayout(sleep)
        sleep_layout.setContentsMargins(20, 16, 20, 16)
        sleep_layout.addWidget(QLabel("昨夜监测时间线（23:00 - 07:30）"))
        bar = QFrame()
        bar.setObjectName("SleepTrack")
        bar.setFixedHeight(18)
        sleep_layout.addWidget(bar)
        sleep_layout.addWidget(QLabel("23:00        01:00        03:00        05:00        07:30"))
        sleep_layout.addWidget(QLabel("体动波动"))
        sleep_layout.addWidget(SleepMotion())
        layout.addWidget(sleep, 2 if self.compact else 1, 0 if self.compact else 1)

        summary = Card()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(20, 16, 20, 16)
        summary_layout.addWidget(QLabel("摘要与建议"))
        summary_layout.addSpacing(8)
        for text, value in [("总事件", "2 次"), ("呼吸异常", "1 次"), ("体动异常", "1 次"), ("较前一晚", "↓ 3 次")]:
            row = QHBoxLayout()
            row.addWidget(QLabel(text))
            row.addStretch()
            value_label = QLabel(value)
            value_label.setStyleSheet(f"color: {RED if '次' in value and '↓' not in value else GREEN}; font-weight: 800;")
            row.addWidget(value_label)
            summary_layout.addLayout(row)
        summary_layout.addSpacing(12)
        summary_layout.addWidget(QLabel("💡  建议"))
        for text in ["保持规律作息，避免熬夜", "睡前减少饮水，降低夜间走动概率", "适当增加日间活动，改善睡眠质量"]:
            bullet = QLabel(f"•  {text}")
            bullet.setObjectName("Bullet")
            bullet.setWordWrap(True)
            summary_layout.addWidget(bullet)
        layout.addWidget(summary, 3 if self.compact else 2, 0 if self.compact else 1)

        bottom = QGridLayout()
        bottom.setHorizontalSpacing(10 if self.compact else 18)
        bottom.setVerticalSpacing(10 if self.compact else 0)
        for i, card in enumerate(
            [
                MetricCard("☾", VIOLET, "#F3EFFD", "平均入睡时间", "23:18", "", "较上周提前 12 分钟 ↑", compact=self.compact),
                MetricCard("门", ORANGE, "#FFF4E6", "夜间走动次数", "1.2", "次", "较上周减少 0.6 次 ↓", compact=self.compact),
                MetricCard("✓", BLUE, "#EDF4FF", "数据有效率", "98.6%", "", "较上周提升 1.2% ↑", compact=self.compact),
            ]
        ):
            bottom.addWidget(card, i // 2 if self.compact else 0, i % 2 if self.compact else i)
        layout.addLayout(bottom, 4 if self.compact else 2, 0)
        return page

    def _history_page_compact(self):
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)
        layout.setColumnStretch(0, 3)
        layout.setColumnStretch(1, 2)
        layout.setRowStretch(1, 1)

        top = QGridLayout()
        top.setHorizontalSpacing(12)
        top_cards = [
            MetricCard("♥", GREEN, "#E9F9F0", "平均心率", "68", "bpm", "较前一晚 ↓3", compact=True),
            MetricCard("肺", PINK, "#FFF0F6", "平均呼吸", "15", "rpm", "较前一晚 ↓1", compact=True),
            MetricCard("时", BLUE, "#EDF4FF", "有效监测时长", "8小时32分", "", "较前一晚 +22分", compact=True),
            MetricCard("!", VIOLET, "#F3EFFD", "异常事件", "2", "次", "较前一晚 ↓3", compact=True),
        ]
        for column, card in enumerate(top_cards):
            card.setFixedHeight(104)
            top.addWidget(card, 0, column)
            top.setColumnStretch(column, 1)
        layout.addLayout(top, 0, 0, 1, 2)

        trend = Card()
        trend_layout = QVBoxLayout(trend)
        trend_layout.setContentsMargins(16, 12, 16, 12)
        trend_head = QHBoxLayout()
        trend_head.addWidget(QLabel("7天生命体征趋势"))
        trend_head.addStretch()
        tab = QLabel("  7天  ")
        tab.setObjectName("TabLabel")
        trend_head.addWidget(tab)
        trend_layout.addLayout(trend_head)
        legend = QLabel("━ 心率 bpm        ━ 呼吸 rpm")
        legend.setStyleSheet(f"color: {BLUE}; font-weight: 700;")
        trend_layout.addWidget(legend)
        trend_layout.addWidget(LineChart([72, 70, 68, 69, 67, 68, 68], BLUE, 40, 100), 1)
        trend_layout.addWidget(LineChart([16, 15, 16, 15, 14, 15, 15], "#FF2F7D", 0, 30), 1)
        layout.addWidget(trend, 1, 0)

        sleep = Card()
        sleep_layout = QVBoxLayout(sleep)
        sleep_layout.setContentsMargins(16, 12, 16, 12)
        sleep_layout.setSpacing(8)
        sleep_layout.addWidget(QLabel("睡眠时间线  23:00 - 07:30"))
        bar = QFrame()
        bar.setObjectName("SleepTrack")
        bar.setFixedHeight(18)
        sleep_layout.addWidget(bar)
        time_axis = QLabel("23:00    01:00    03:00    05:00    07:30")
        time_axis.setObjectName("SmallText")
        sleep_layout.addWidget(time_axis)
        sleep_layout.addWidget(QLabel("体动波动"))
        sleep_layout.addWidget(SleepMotion(), 1)
        layout.addWidget(sleep, 1, 1)

        summary = MonitorStatusBar(compact=True)
        summary.alarm_label["value"].setText("2 次")
        summary.motion_label["value"].setText("呼吸异常 1 次  ·  体动异常 1 次")
        summary.frame_label["value"].setText("98.6%")
        summary.parse_err_label["value"].setText("↓3")
        summary.crc_err_label["value"].setText("良好")
        layout.addWidget(summary, 2, 0, 1, 2)
        return page

    def _device_page(self):
        if self.compact:
            return self._device_page_compact()

        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(10 if self.compact else 18, 10 if self.compact else 18, 10 if self.compact else 18, 10 if self.compact else 18)
        layout.setHorizontalSpacing(10 if self.compact else 14)
        layout.setVerticalSpacing(10 if self.compact else 14)
        layout.setColumnStretch(0, 1)
        layout.setRowStretch(1, 1)

        status = QGridLayout()
        status.setHorizontalSpacing(10 if self.compact else 14)
        status.setVerticalSpacing(10 if self.compact else 0)
        for i, card in enumerate(
            [
                DeviceStatusCard("⌁", GREEN, "#E9F9F0", "雷达链路", "正常", "信号强度：-32 dBm", "帧率：98.6 fps", self.compact),
                DeviceStatusCard("▣", BLUE, "#EDF4FF", "视频链路", "已连接", "分辨率：1920x1080", "码率：2.1 Mbps", self.compact),
                DeviceStatusCard("↗", VIOLET, "#F3EFFD", "数据解析", "正常", "解析帧率：98.6 fps", "丢包率：0.3%", self.compact),
                DeviceStatusCard("▤", ORANGE, "#FFF4E6", "存储服务", "正常", "可用空间：186.5 GB", "写入速率：12.4 MB/s", self.compact),
            ]
        ):
            status.addWidget(card, i // 2 if self.compact else 0, i % 2 if self.compact else i)
        layout.addLayout(status, 0, 0, 1, 1 if self.compact else 3)

        debug = Card()
        debug_layout = QVBoxLayout(debug) if self.compact else QHBoxLayout(debug)
        debug_layout.setContentsMargins(12 if self.compact else 18, 12 if self.compact else 18, 12 if self.compact else 18, 12 if self.compact else 18)
        info = QVBoxLayout()
        info.addWidget(QLabel("调试信息"))
        for line in ["串口              COM6", "波特率          921600", "帧率            98.6 fps", "延迟            12 ms", "视频源      192.168.1.106", "RTSP状态          已连接", "MQTT状态          已连接"]:
            info.addWidget(QLabel(line))
        info.addStretch()
        debug_layout.addLayout(info, 1)
        logs = QVBoxLayout()
        log_now = datetime.now()
        for offset, message in [
            (6, "串口数据接收正常，帧号实时更新"),
            (5, "雷达数据解析成功，心率/呼吸来自接口"),
            (4, "视频帧接收正常"),
            (3, "RTSP连接正常"),
            (2, "MQTT心跳正常"),
            (1, "数据存储正常"),
            (0, "所有模块运行正常"),
        ]:
            line = f"● {(log_now - timedelta(seconds=offset)).strftime('%H:%M:%S')}  {message}"
            label = QLabel(line)
            label.setObjectName("LogLine")
            label.setWordWrap(True)
            logs.addWidget(label)
        logs.addStretch()
        debug_layout.addLayout(logs, 2)
        layout.addWidget(debug, 1, 0)

        config = Card()
        config_layout = QVBoxLayout(config)
        config_layout.setContentsMargins(18, 18, 18, 18)
        config_layout.addWidget(QLabel("参数配置"))
        for line in ["波特率        921600  ˅", "离线阈值        -   10   + 秒", "采样窗口        -   30   + 秒", "视频源地址", "rtsp://192.168.1.106:554/stream"]:
            label = QLabel(line)
            label.setObjectName("ConfigLine")
            config_layout.addWidget(label)
        save = QPushButton("▣  保存配置")
        save.setObjectName("SaveButton")
        config_layout.addWidget(save)
        config_layout.addStretch()
        layout.addWidget(config, 2 if self.compact else 1, 0 if self.compact else 1)

        side = QVBoxLayout()
        warning = SideCard("当前告警", "🚨")
        warning.add_row("●  高优先级", "0")
        warning.add_row("●  中优先级", "1")
        warning.add_row("●  低优先级", "2")
        warning.add_row("查看全部  ›", "")
        side.addWidget(warning)
        fault = SideCard("故障建议", "💡")
        for text in ["系统运行正常，暂无故障", "建议保持设备通风良好", "定期检查镜头清洁情况", "如出现异常数据，请重启设备或检查网络连接"]:
            fault.add_bullet(text)
        side.addWidget(fault, 1)
        side_box = QWidget()
        if not self.compact:
            side_box.setFixedWidth(270)
        side_box.setLayout(side)
        layout.addWidget(side_box, 3 if self.compact else 1, 0 if self.compact else 2)

        bottom = QGridLayout()
        bottom.setHorizontalSpacing(10 if self.compact else 12)
        bottom.setVerticalSpacing(10 if self.compact else 0)
        mini_cards = [
            self._mini_card("有效帧率", "98.6 fps", BarSpark(BLUE, [8, 12, 18, 24, 22, 27, 19, 26, 28, 20, 25, 23, 30])),
            self._mini_card("CRC错误", "0", BarSpark(GREEN, [3, 9, 17, 8, 20, 5, 13, 18, 7, 4, 9, 6])),
            self._mini_card("解析错误", "1", BarSpark(ORANGE, [12, 4, 18, 10, 14, 8, 16, 22, 9, 6, 11])),
        ]
        q = QualityWave()
        q.setMinimumHeight(44)
        mini_cards.extend(
            [
                self._mini_card("波形质量", "良好", q),
                self._mini_card("数据采集", "● 正常", QLabel("雷达 + 摄像头")),
                self._mini_card("协议解析", "● 正常", QLabel("雷达协议 + RTSP")),
                self._mini_card("本地存储", "● 正常", QLabel("可用空间：186.5 GB")),
            ]
        )
        for i, card in enumerate(mini_cards):
            bottom.addWidget(card, i // 2 if self.compact else 0, i % 2 if self.compact else i)
        layout.addLayout(bottom, 4 if self.compact else 2, 0, 1, 1 if self.compact else 3)
        return page

    def _bedside_image_page(self):
        if self.compact:
            return self._bedside_image_page_compact()

        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(18)
        layout.setColumnStretch(0, 13)
        layout.setColumnStretch(1, 7)
        layout.setRowStretch(0, 0)
        layout.setRowStretch(1, 1)
        layout.setRowStretch(2, 0)

        top = QGridLayout()
        top.setHorizontalSpacing(24)
        self.image_status_card = MetricCard("▣", BLUE, "#EDF4FF", "影像状态", "观察中", "", "●  视频流已连接", compact=False)
        self.image_bed_card = MetricCard("质", GREEN, "#E9F9F0", "画面质量", "清晰", "", "●  完整显示", compact=False)
        self.image_safety_card = MetricCard("!", VIOLET, "#F3EFFD", "安全提醒", "正常", "", "●  周围环境安全", compact=False)
        top_cards = [
            self.image_status_card,
            self.image_bed_card,
            self.image_safety_card,
        ]
        for column, card in enumerate(top_cards):
            card.setFixedHeight(124)
            top.addWidget(card, 0, column)
            top.setColumnStretch(column, 1)
        layout.addLayout(top, 0, 0, 1, 2)

        image_card = Card()
        image_layout = QVBoxLayout(image_card)
        image_layout.setContentsMargins(18, 16, 18, 18)
        image_layout.setSpacing(13)
        head = QHBoxLayout()
        title = QLabel("当前床旁画面")
        title.setObjectName("CareTitle")
        self.capture_status_text = QLabel("RK3576 从机已连接，支持图片上报与视频流观察  ⓘ")
        self.capture_status_text.setObjectName("CareSub")
        head.addWidget(title)
        head.addSpacing(34)
        head.addWidget(self.capture_status_text)
        head.addStretch()
        image_layout.addLayout(head)

        self.capture_preview = BedsideImagePreview(ASSET_DIR / "bedside_image_preview.jpg")
        image_layout.addWidget(self.capture_preview, 1)

        actions = QGridLayout()
        actions.setHorizontalSpacing(18)
        for column, (text, primary, slot) in enumerate([
            ("▣  拍摄一张", False, self._capture_once),
            ("▶  开始观察", True, self._start_observe),
            ("Ⅱ  暂停观察", False, self._stop_observe),
            ("▤  保存记录", False, self._save_capture),
        ]):
            button = QPushButton(text)
            button.setObjectName("PrimaryImageButton" if primary else "ImageButton")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(slot)
            actions.addWidget(button, 0, column)
            actions.setColumnStretch(column, 1)
        image_layout.addLayout(actions)
        layout.addWidget(image_card, 1, 0)

        side = QVBoxLayout()
        side.setSpacing(18)
        self.nursing_panel = NursingRecordPanel()
        self.recent_record_panel = RecentRecordPanel()
        self.recent_record_panel.on_more = self._show_recent_records
        side.addWidget(self.nursing_panel, 3)
        side.addWidget(self.recent_record_panel, 2)
        side_box = QWidget()
        side_box.setLayout(side)
        side_box.setMinimumWidth(360)
        layout.addWidget(side_box, 1, 1)

        summary = Card()
        summary.setObjectName("ImageSummary")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(34, 0, 34, 0)
        summary_layout.setSpacing(22)
        icon = QLabel("✓")
        icon.setObjectName("SummaryShield")
        icon.setAlignment(Qt.AlignCenter)
        summary_layout.addWidget(icon)
        text = QLabel("影像画面清晰，雷达链路正常，生命体征平稳。")
        text.setWordWrap(True)
        text.setStyleSheet("color: #142033; font-size: 23px; font-weight: 900;")
        summary_layout.addWidget(text)
        summary_layout.addStretch()
        layout.addWidget(summary, 2, 0, 1, 2)
        return page

    def _bedside_image_page_compact(self):
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)
        layout.setColumnStretch(0, 13)
        layout.setColumnStretch(1, 7)
        layout.setRowStretch(0, 0)
        layout.setRowStretch(1, 1)
        layout.setRowStretch(2, 0)

        top = QGridLayout()
        top.setHorizontalSpacing(12)
        self.image_status_card = MetricCard("▣", BLUE, "#EDF4FF", "影像状态", "观察中", "", "● 已连接", compact=True)
        self.image_bed_card = MetricCard("质", GREEN, "#E9F9F0", "画面质量", "清晰", "", "● 完整显示", compact=True)
        self.image_safety_card = MetricCard("!", VIOLET, "#F3EFFD", "安全提醒", "正常", "", "● 安全", compact=True)
        for column, card in enumerate([self.image_status_card, self.image_bed_card, self.image_safety_card]):
            card.setFixedHeight(104)
            top.addWidget(card, 0, column)
            top.setColumnStretch(column, 1)
        layout.addLayout(top, 0, 0, 1, 2)

        image_card = Card()
        image_layout = QVBoxLayout(image_card)
        image_layout.setContentsMargins(12, 10, 12, 12)
        image_layout.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("当前床旁画面")
        title.setObjectName("CareTitle")
        self.capture_status_text = QLabel("RK3576 从机已连接，支持图片上报与视频流观察")
        self.capture_status_text.setObjectName("CareSub")
        head.addWidget(title)
        head.addSpacing(18)
        head.addWidget(self.capture_status_text)
        head.addStretch()
        image_layout.addLayout(head)
        self.capture_preview = BedsideImagePreview(ASSET_DIR / "bedside_image_preview.jpg")
        image_layout.addWidget(self.capture_preview, 1)
        actions = QGridLayout()
        actions.setHorizontalSpacing(10)
        actions.setVerticalSpacing(8)
        for column, (text, slot) in enumerate([
            ("▣ 拍摄一张", self._capture_once),
            ("▶ 开始观察", self._start_observe),
            ("Ⅱ 暂停观察", self._stop_observe),
            ("▤ 保存记录", self._save_capture),
        ]):
            button = QPushButton(text)
            button.setObjectName("PrimaryImageButton" if column == 1 else "ImageButton")
            button.clicked.connect(slot)
            actions.addWidget(button, 0, column)
            actions.setColumnStretch(column, 1)
        image_layout.addLayout(actions)
        layout.addWidget(image_card, 1, 0)

        side = QVBoxLayout()
        side.setSpacing(12)
        self.nursing_panel = NursingRecordPanel()
        self.recent_record_panel = RecentRecordPanel()
        self.recent_record_panel.on_more = self._show_recent_records
        side.addWidget(self.nursing_panel, 3)
        side.addWidget(self.recent_record_panel, 2)
        side_box = QWidget()
        side_box.setLayout(side)
        side_box.setMinimumWidth(320)
        layout.addWidget(side_box, 1, 1)

        summary = Card()
        summary.setObjectName("ImageSummary")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(18, 0, 18, 0)
        summary_layout.setSpacing(14)
        icon = QLabel("✓")
        icon.setObjectName("SummaryShield")
        icon.setAlignment(Qt.AlignCenter)
        summary_layout.addWidget(icon)
        text = QLabel("影像画面清晰，雷达链路正常，生命体征平稳。")
        text.setWordWrap(True)
        text.setStyleSheet("color: #142033; font-size: 18px; font-weight: 900;")
        summary_layout.addWidget(text)
        summary_layout.addStretch()
        layout.addWidget(summary, 2, 0, 1, 2)
        return page

    def _device_page_compact(self):
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)
        layout.setColumnStretch(0, 3)
        layout.setColumnStretch(1, 2)
        layout.setRowStretch(1, 1)

        status = QGridLayout()
        status.setHorizontalSpacing(12)
        status_cards = [
            DeviceStatusCard("⌁", GREEN, "#E9F9F0", "雷达链路", "正常", "串口：/dev/ttyS9", "波特率：115200", compact=True),
            DeviceStatusCard("▣", BLUE, "#EDF4FF", "串口状态", "已打开", "权限：dialout", "读取超时：0.2s", compact=True),
            DeviceStatusCard("↗", VIOLET, "#F3EFFD", "数据解析", "正常", "协议：R60ABD1", "校验：启用", compact=True),
            DeviceStatusCard("▤", ORANGE, "#FFF4E6", "API服务", "正常", "端口：8000", "接口：/radar", compact=True),
        ]
        for column, card in enumerate(status_cards):
            card.setFixedHeight(104)
            status.addWidget(card, 0, column)
            status.setColumnStretch(column, 1)
        layout.addLayout(status, 0, 0, 1, 2)

        self.serial_panel = SerialDataPanel()
        layout.addWidget(self.serial_panel, 1, 0)

        side = Card()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(16, 12, 16, 12)
        side_layout.setSpacing(8)
        side_layout.addWidget(QLabel("最后一帧 HEX"))
        self.last_hex_label = QLabel("-")
        self.last_hex_label.setObjectName("HexLine")
        self.last_hex_label.setWordWrap(True)
        self.last_hex_label.setMaximumHeight(62)
        side_layout.addWidget(self.last_hex_label)
        side_layout.addSpacing(4)
        side_layout.addWidget(QLabel("运行状态"))
        for line in [
            "● 串口 /dev/ttyS9",
            "● 波特率 115200",
            "● FastAPI :8000",
            "● UI 1s 刷新",
        ]:
            label = QLabel(line)
            label.setObjectName("LogLine")
            label.setWordWrap(True)
            side_layout.addWidget(label)
        side_layout.addSpacing(8)
        side_layout.addWidget(QLabel("板端参数"))
        for line in [
            "波形缓存    300点",
            "采样周期    200 ms",
            "启动脚本    ./run_board.sh",
        ]:
            label = QLabel(line)
            label.setObjectName("ConfigLine")
            side_layout.addWidget(label)
        side_layout.addStretch()
        layout.addWidget(side, 1, 1)

        summary = MonitorStatusBar(compact=True)
        summary.alarm_label["value"].setText("98.6 fps")
        summary.motion_label["value"].setText("串口正常  ·  API正常")
        summary.frame_label["value"].setText("0")
        summary.parse_err_label["value"].setText("0")
        summary.crc_err_label["value"].setText("0")
        layout.addWidget(summary, 2, 0, 1, 2)
        return page

    def _mini_card(self, title: str, value: str, widget: QWidget):
        card = Card()
        card.setMinimumHeight(104 if self.compact else 128)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10 if self.compact else 12, 10 if self.compact else 12, 10 if self.compact else 12, 10 if self.compact else 12)
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setObjectName("StatTitle")
        value_label = QLabel(value)
        value_label.setAlignment(Qt.AlignCenter)
        value_label.setStyleSheet(f"color: {BLUE if '98' in value or '正常' in value else GREEN}; font-size: {18 if self.compact else 22}px; font-weight: 900;")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(widget)
        return card

    def _activate(self, index: int):
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

    def _toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() <= (54 if self.compact else 70):
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.drag_pos and event.buttons() & Qt.LeftButton and not self.isMaximized():
            self.move(event.globalPosition().toPoint() - self.drag_pos)

    def mouseReleaseEvent(self, event):
        self.drag_pos = None

    def _apply_style(self):
        root_font = 12 if self.compact else 14
        logo_size = 86
        logo_font = 0
        nav_height = 58
        nav_padding = 16
        nav_font = 16
        title_font = 18 if self.compact else 23
        window_button_size = 30 if self.compact else 32
        window_button_font = 20 if self.compact else 22
        side_title_font = 14 if self.compact else 15
        save_font = 14 if self.compact else 16
        self.setStyleSheet(
            f"""
            QWidget#Root {{
                background: {BG};
                color: {TEXT};
                font-family: "Microsoft YaHei UI", "Microsoft YaHei";
                font-size: {root_font}px;
            }}
            QFrame#Sidebar {{
                background: white;
                border-right: 1px solid #D5DDE8;
            }}
            QLabel#Logo {{
                min-width: {logo_size}px;
                max-width: {logo_size}px;
                min-height: {logo_size}px;
                max-height: {logo_size}px;
                border-radius: 16px;
                background: white;
            }}
            QPushButton#NavButton {{
                height: {nav_height}px;
                padding-left: {nav_padding}px;
                border: 0;
                background: transparent;
                color: #2B3445;
                text-align: left;
                font-size: {nav_font}px;
                font-weight: 700;
            }}
            QPushButton#NavButton:checked {{
                color: {BLUE};
                background: #EEF5FF;
                border-left: 4px solid {BLUE};
            }}
            QLabel#Collapse {{
                color: #4D596C;
                font-size: 14px;
            }}
            QFrame#Titlebar {{
                background: rgba(255, 255, 255, 0.96);
                border-bottom: 1px solid #D5DDE8;
            }}
            QLabel#WindowTitle {{
                font-size: {title_font}px;
                font-weight: 900;
            }}
            QPushButton#WindowButton {{
                border: 0;
                background: transparent;
                color: #0B1320;
                min-width: {window_button_size}px;
                min-height: {window_button_size}px;
                font-size: {window_button_font}px;
            }}
            QPushButton#WindowButton:hover {{
                background: #EEF2F7;
                border-radius: 6px;
            }}
            QFrame#OnlinePill {{
                background: #EDF9F2;
                border: 1px solid #CFEAD9;
                border-radius: 10px;
                min-height: 38px;
                max-height: 38px;
            }}
            QLabel#GreenDot {{
                background: {GREEN};
                border-radius: 5px;
            }}
            QLabel#OnlineText {{
                color: {GREEN};
                font-size: 15px;
                font-weight: 800;
            }}
            QFrame#Card, QFrame#WavePanel {{
                background: white;
                border: 1px solid {LINE};
                border-radius: 8px;
            }}
            QFrame#WavePanel {{
                background: #061733;
            }}
            QLabel#MetricTitle, QLabel#StatTitle {{
                color: #222B3A;
                font-weight: 800;
            }}
            QLabel#Unit {{
                color: #2F394B;
                font-size: 15px;
            }}
            QLabel#FooterText, QLabel#SmallText, QLabel#SideRow, QLabel#Bullet {{
                color: {MUTED};
                font-size: 12px;
            }}
            QLabel#SideTitle {{
                color: #202938;
                font-size: {side_title_font}px;
                font-weight: 900;
            }}
            QFrame#DashLine {{
                color: #D5DDE8;
                border: 0;
                border-left: 1px dashed #D5DDE8;
            }}
            QLabel#TabLabel {{
                color: white;
                background: {BLUE};
                border-radius: 5px;
                padding: 6px 18px;
            }}
            QFrame#SleepTrack {{
                border-radius: 3px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #35C76C, stop:0.30 #35C76C, stop:0.31 #F6CF48,
                    stop:0.40 #35C76C, stop:0.73 #35C76C, stop:0.78 #9BA5B1,
                    stop:0.86 #35C76C, stop:1 #F6CF48);
            }}
            QLabel#LogLine {{
                color: {GREEN};
                font-size: 12px;
            }}
            QLabel#DenseValue {{
                color: #111827;
                font-size: 11px;
                font-weight: 800;
            }}
            QLabel#FieldGroupTitle {{
                color: {BLUE};
                font-size: 13px;
                font-weight: 900;
                padding-top: 4px;
            }}
            QLabel#HexLine {{
                color: #46536A;
                font-family: Consolas, "Microsoft YaHei UI";
                font-size: 10px;
                border: 1px solid {LINE};
                border-radius: 5px;
                padding: 6px 7px;
                background: #FBFDFF;
            }}
            QLabel#ConfigLine {{
                color: #46536A;
                border: 1px solid {LINE};
                border-radius: 5px;
                padding: 6px 8px;
                background: #FBFDFF;
            }}
            QPushButton#SaveButton {{
                min-height: 42px;
                border: 0;
                border-radius: 7px;
                background: {BLUE};
                color: white;
                font-weight: 900;
                font-size: {save_font}px;
            }}
            QLabel#CareTitle {{
                color: #101828;
                font-size: {18 if self.compact else 20}px;
                font-weight: 900;
            }}
            QLabel#CareSub {{
                color: {MUTED};
                font-size: {12 if self.compact else 14}px;
                font-weight: 700;
            }}
            QLabel#BedsideImage {{
                background: #E9EEF6;
                border: 1px solid #CFD8E6;
                border-radius: 8px;
                color: {MUTED};
                font-weight: 800;
            }}
            QPushButton#ImageButton, QPushButton#PrimaryImageButton {{
                min-height: {44 if self.compact else 52}px;
                border-radius: 7px;
                font-size: {13 if self.compact else 16}px;
                font-weight: 900;
            }}
            QPushButton#ImageButton {{
                background: white;
                color: #142033;
                border: 1px solid #D5DDE8;
            }}
            QPushButton#ImageButton:hover {{
                background: #F6FAFF;
                border-color: #BFD5F5;
            }}
            QPushButton#PrimaryImageButton {{
                background: {BLUE};
                color: white;
                border: 1px solid {BLUE};
            }}
            QLabel#CareIcon {{
                color: #516079;
                font-size: 20px;
                font-weight: 900;
            }}
            QFrame#CareRow {{
                background: transparent;
                border: 0;
            }}
            QLabel#CareLabel {{
                color: #46536A;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton#MoreButton {{
                border: 0;
                background: transparent;
                color: {BLUE};
                font-size: 13px;
                font-weight: 900;
                padding: 2px 0;
            }}
            QPushButton#MoreButton:hover {{
                color: #0B55D9;
            }}
            QLabel#ImageBadge {{
                color: {BLUE};
                background: #EAF3FF;
                border-radius: 5px;
                padding: 3px 6px;
                font-size: 11px;
                font-weight: 800;
            }}
            QDialog#RecordsDialog {{
                background: {BG};
            }}
            QLabel#DialogTitle {{
                color: {TEXT};
                font-size: 22px;
                font-weight: 900;
            }}
            QScrollArea#RecordsScroll {{
                border: 0;
                background: transparent;
            }}
            QFrame#RecordListRow {{
                background: white;
                border: 1px solid #DCE4EF;
                border-radius: 8px;
            }}
            QLabel#RecordTime {{
                color: #5B667A;
                font-size: 13px;
                font-weight: 800;
                min-width: 108px;
            }}
            QLabel#RecordText {{
                color: {TEXT};
                font-size: 14px;
                font-weight: 900;
            }}
            QFrame#ImageSummary {{
                background: #EEF5FF;
                border: 1px solid #BFD7FF;
                border-radius: 8px;
                min-height: {78 if self.compact else 96}px;
                max-height: {78 if self.compact else 96}px;
            }}
            QLabel#SummaryShield {{
                min-width: 54px;
                max-width: 54px;
                min-height: 54px;
                max-height: 54px;
                border-radius: 27px;
                background: {BLUE};
                color: white;
                font-size: 32px;
                font-weight: 900;
            }}
            """
        )


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = HealthMonitorWindow()
    if window.compact:
        window.showFullScreen()
    else:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
