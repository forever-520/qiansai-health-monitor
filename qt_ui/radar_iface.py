# =========================================================
# radar_iface.py — UI 与底层 UART 之间的专用接口层
# main.py 只依赖本文件，不直接访问 UART_2 内部字典结构。
# 后期新增字段：在 VitalsSnapshot 加属性 + 在 get_vitals() 里赋值即可。
# =========================================================

import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import List
from urllib.request import urlopen

MOCK_MODE = os.environ.get("RADAR_MOCK", "").strip() == "1"

if not MOCK_MODE:
    import UART_2
else:
    UART_2 = None

# =========================================================
# 文本映射 — UI 层通过本文件统一访问，不直接引用 UART_2 常量
# =========================================================
EXIST_TEXT       = UART_2.HUMAN_EXIST_TEXT if UART_2 else {0: "无人", 1: "有人"}
MOTION_TEXT      = UART_2.MOVE_STATE_TEXT if UART_2 else {0: "无人", 1: "静止", 2: "活跃"}
BREATH_STATE_TEXT = UART_2.BREATH_STATE_TEXT if UART_2 else {1: "正常", 2: "呼吸过高", 3: "呼吸过低", 4: "无呼吸"}
BED_TEXT         = UART_2.BED_STATE_TEXT if UART_2 else {0: "离床", 1: "入床", 2: "无"}
SLEEP_STATE_TEXT = UART_2.SLEEP_STATE_TEXT if UART_2 else {0: "深睡", 1: "浅睡", 2: "清醒", 3: "无人"}
SLEEP_EXCEPTION_TEXT = (
    UART_2.SLEEP_EXCEPTION_TEXT if UART_2 else {0: "睡眠不足4小时", 1: "睡眠超过12小时", 2: "长时间异常无人", 3: "无异常"}
)
SLEEP_RATING_TEXT = UART_2.SLEEP_RATING_TEXT if UART_2 else {0: "无", 1: "良好", 2: "一般", 3: "较差"}
STRUGGLE_TEXT = UART_2.STRUGGLE_TEXT if UART_2 else {0: "无", 1: "有"}
NOBODY_TIMER_TEXT = UART_2.NOBODY_TIMER_TEXT if UART_2 else {0: "关闭", 1: "开启"}


# =========================================================
# 数据结构 — UI 层所需字段，全部有类型与默认值
# 新增字段：在此处追加属性，再在 get_vitals() 里从 snapshot 赋值
# =========================================================
@dataclass
class VitalsSnapshot:
    # ---------- 人体存在 ----------
    exist: int = 0          # 0=无人  1=有人
    motion_val: int = 0     # 体动强度 0~100
    motion_state: int = 0   # 0=无人  1=静止  2=活跃
    distance: int = 0        # cm
    x: int = 0               # cm
    y: int = 0               # cm
    z: int = 0               # cm

    # ---------- 心率 ----------
    heart_rate: int = 0                          # bpm
    heart_wave: List[int] = field(default_factory=list)

    # ---------- 呼吸 ----------
    breath_rate: int = 0                         # 次/min
    breath_state: int = 0                        # 1=正常 2=过高 3=过低 4=无呼吸
    breath_wave: List[int] = field(default_factory=list)

    # ---------- 睡眠 / 在床 ----------
    bed: int = 0            # 0=离床  1=入床  2=无
    sleep_state: int = 3    # 0=深睡  1=浅睡  2=清醒  3=无人
    awake_time: int = 0
    light_time: int = 0
    deep_time: int = 0
    score: int = 0
    avg_breath: int = 0
    avg_heart: int = 0
    turn_over: int = 0
    big_motion_ratio: int = 0
    small_motion_ratio: int = 0
    apnea: int = 0
    total_sleep: int = 0
    awake_ratio: int = 0
    light_ratio: int = 0
    deep_ratio: int = 0
    out_bed_time: int = 0
    out_bed_count: int = 0
    exception: int = 3
    rating: int = 0
    struggle: int = 0
    nobody_timer: int = 0

    # ---------- 系统诊断 ----------
    frame_count: int = 0
    parse_error_count: int = 0
    checksum_error_count: int = 0
    last_frame_hex: str = ""
    last_frame_ms: int = 0


def _wave(length: int, phase: float, amplitude: int, drift: int = 0) -> List[int]:
    return [
        int(math.sin((i + phase) / 8.0) * amplitude + math.sin((i + phase) / 23.0) * drift)
        for i in range(length)
    ]


DISPLAY_WAVE_LEN = 300
HEART_RATE_MIN = 40
BREATH_RATE_MIN = 6
HEART_PHASE_STEP = 9.0
BREATH_PHASE_STEP = 4.5

_remote_runtime_enabled = False
_remote_snapshot_lock = threading.Lock()
_remote_snapshot = None

_display_state = {
    "heart_rate": 0,
    "breath_rate": 0,
    "heart_phase": 0.0,
    "breath_phase": 0.0,
}


def _reset_display_state() -> None:
    _display_state["heart_rate"] = 0
    _display_state["breath_rate"] = 0
    _display_state["heart_phase"] = 0.0
    _display_state["breath_phase"] = 0.0


def _filter_display_rate(kind: str, candidate: int) -> int:
    min_value = HEART_RATE_MIN if kind == "heart" else BREATH_RATE_MIN
    if candidate >= min_value:
        _display_state[f"{kind}_rate"] = candidate
        return candidate
    # 实测数据无效时直接显示 0，不保留上一次有效心率/呼吸，避免 UI 看起来卡住。
    _display_state[f"{kind}_rate"] = 0
    return max(candidate, 0)


def _wave_is_usable(values: List[int]) -> bool:
    if len(values) < 24:
        return False
    return len(set(values)) > 2


def _display_wave(kind: str, rate: int, source_wave: List[int]) -> List[int]:
    if rate <= 0:
        return []
    if _wave_is_usable(source_wave):
        return list(source_wave)
    return []


def _mock_vitals() -> VitalsSnapshot:
    t = time.time()
    heart = 78 + int(math.sin(t / 4) * 4)
    breath = 18 + int(math.sin(t / 5) * 2)
    motion = 12 + int(abs(math.sin(t / 3)) * 28)
    frames = int(t * 5) % 100000
    return VitalsSnapshot(
        exist=1,
        motion_val=motion,
        motion_state=1 if motion < 20 else 2,
        distance=35 + int(math.sin(t / 6) * 4),
        x=int(math.sin(t / 4) * 8),
        y=28 + int(math.cos(t / 5) * 4),
        z=0,
        heart_rate=heart,
        heart_wave=_wave(300, t * 8, 62, 10),
        breath_rate=breath,
        breath_state=1,
        breath_wave=_wave(300, t * 5, 28, 7),
        bed=1,
        sleep_state=2,
        awake_time=42,
        light_time=236,
        deep_time=178,
        score=86,
        avg_breath=16,
        avg_heart=72,
        turn_over=4,
        big_motion_ratio=8,
        small_motion_ratio=19,
        apnea=0,
        total_sleep=456,
        awake_ratio=9,
        light_ratio=52,
        deep_ratio=39,
        out_bed_time=6,
        out_bed_count=1,
        exception=3,
        rating=1,
        struggle=0,
        nobody_timer=0,
        frame_count=frames,
        parse_error_count=0,
        checksum_error_count=0,
        last_frame_hex="53 59 85 02 00 01 4E 82 54 43",
        last_frame_ms=int(t * 1000),
    )


def _remote_url_from_env() -> str:
    return os.environ.get("RADAR_REMOTE_URL", "").strip()


def _remote_vitals_from_snapshot(snap: dict) -> VitalsSnapshot:
    sleep = snap.get("sleep", {})
    heart_rate = snap["heart"]["rate"]
    breath_rate = snap["breath"]["rate"]
    heart_wave = _display_wave("heart", heart_rate, snap["heart"].get("wave", []))
    breath_wave = _display_wave("breath", breath_rate, snap["breath"].get("wave", []))
    return VitalsSnapshot(
        exist=snap["human"]["exist"],
        motion_val=snap["human"]["motion_val"],
        motion_state=snap["human"]["motion_state"],
        distance=snap["human"]["distance"],
        x=snap["human"]["x"],
        y=snap["human"]["y"],
        z=snap["human"]["z"],
        heart_rate=heart_rate,
        heart_wave=heart_wave,
        breath_rate=breath_rate,
        breath_state=snap["breath"]["state"],
        breath_wave=breath_wave,
        bed=sleep.get("bed", 0),
        sleep_state=sleep.get("state", 3),
        awake_time=sleep.get("awake_time", 0),
        light_time=sleep.get("light_time", 0),
        deep_time=sleep.get("deep_time", 0),
        score=sleep.get("score", 0),
        avg_breath=sleep.get("avg_breath", 0),
        avg_heart=sleep.get("avg_heart", 0),
        turn_over=sleep.get("turn_over", 0),
        big_motion_ratio=sleep.get("big_motion_ratio", 0),
        small_motion_ratio=sleep.get("small_motion_ratio", 0),
        apnea=sleep.get("apnea", 0),
        total_sleep=sleep.get("total_sleep", 0),
        awake_ratio=sleep.get("awake_ratio", 0),
        light_ratio=sleep.get("light_ratio", 0),
        deep_ratio=sleep.get("deep_ratio", 0),
        out_bed_time=sleep.get("out_bed_time", 0),
        out_bed_count=sleep.get("out_bed_count", 0),
        exception=sleep.get("exception", 3),
        rating=sleep.get("rating", 0),
        struggle=sleep.get("struggle", 0),
        nobody_timer=sleep.get("nobody_timer", 0),
        frame_count=snap["system"]["frame_count"],
        parse_error_count=snap["system"]["parse_error_count"],
        checksum_error_count=snap["system"]["checksum_error_count"],
        last_frame_hex=snap["system"]["last_frame_hex"],
        last_frame_ms=int(snap["system"].get("last_frame_ms") or snap["system"].get("last_frame_ts", 0) * 1000 or snap.get("timestamp_ms", 0) or 0),
    )


def _remote_vitals(remote_url: str) -> VitalsSnapshot:
    with urlopen(remote_url, timeout=3) as response:
        snap = __import__("json").loads(response.read().decode("utf-8"))
    return _remote_vitals_from_snapshot(snap)


def _remote_poll_loop(remote_url: str) -> None:
    global _remote_snapshot
    while True:
        try:
            with urlopen(remote_url, timeout=3) as response:
                snap = __import__("json").loads(response.read().decode("utf-8"))
            with _remote_snapshot_lock:
                _remote_snapshot = snap
        except Exception:
            pass
        time.sleep(0.25)


# =========================================================
# 核心接口函数
# =========================================================
def get_vitals() -> VitalsSnapshot:
    """返回当前雷达数据快照，UI 层唯一的数据入口。"""
    if MOCK_MODE:
        return _mock_vitals()

    remote_url = _remote_url_from_env()
    if remote_url:
        if _remote_runtime_enabled:
            with _remote_snapshot_lock:
                snap = _remote_snapshot
            if snap is None:
                return VitalsSnapshot()
            return _remote_vitals_from_snapshot(snap)
        return _remote_vitals(remote_url)

    snap, _ = UART_2.get_snapshot()
    heart_rate = _filter_display_rate("heart", int(snap["heart"].get("rate", 0)))
    breath_rate = _filter_display_rate("breath", int(snap["breath"].get("rate", 0)))
    vital_active = heart_rate > 0 or breath_rate > 0

    # UI 展示兜底：部分雷达固件会先持续上报心率/呼吸，但人体存在/在床状态延迟或仍为无人。
    # 原始数据仍保留在 UART_2 中；这里仅避免监护屏出现“有生命体征但显示无人”的演示冲突。
    display_exist = snap["human"]["exist"]
    display_motion_state = snap["human"]["motion_state"]
    display_bed = snap["sleep"]["bed"]
    display_sleep_state = snap["sleep"]["state"]
    if vital_active and display_exist == 0:
        display_exist = 1
        display_bed = 1
        display_sleep_state = 2
        if display_motion_state == 0:
            display_motion_state = 1

    return VitalsSnapshot(
        exist                = display_exist,
        motion_val           = snap["human"]["motion_val"],
        motion_state         = display_motion_state,
        distance             = snap["human"]["distance"],
        x                    = snap["human"]["x"],
        y                    = snap["human"]["y"],
        z                    = snap["human"]["z"],
        heart_rate           = heart_rate,
        heart_wave           = snap["heart"]["wave"],
        breath_rate          = breath_rate,
        breath_state         = snap["breath"]["state"],
        breath_wave          = snap["breath"]["wave"],
        bed                  = display_bed,
        sleep_state          = display_sleep_state,
        awake_time           = snap["sleep"]["awake_time"],
        light_time           = snap["sleep"]["light_time"],
        deep_time            = snap["sleep"]["deep_time"],
        score                = snap["sleep"]["score"],
        avg_breath           = snap["sleep"]["avg_breath"],
        avg_heart            = snap["sleep"]["avg_heart"],
        turn_over            = snap["sleep"]["turn_over"],
        big_motion_ratio     = snap["sleep"]["big_motion_ratio"],
        small_motion_ratio   = snap["sleep"]["small_motion_ratio"],
        apnea                = snap["sleep"]["apnea"],
        total_sleep          = snap["sleep"]["total_sleep"],
        awake_ratio          = snap["sleep"]["awake_ratio"],
        light_ratio          = snap["sleep"]["light_ratio"],
        deep_ratio           = snap["sleep"]["deep_ratio"],
        out_bed_time         = snap["sleep"]["out_bed_time"],
        out_bed_count        = snap["sleep"]["out_bed_count"],
        exception            = snap["sleep"]["exception"],
        rating               = snap["sleep"]["rating"],
        struggle             = snap["sleep"]["struggle"],
        nobody_timer         = snap["sleep"]["nobody_timer"],
        frame_count          = snap["system"]["frame_count"],
        parse_error_count    = snap["system"]["parse_error_count"],
        checksum_error_count = snap["system"]["checksum_error_count"],
        last_frame_hex       = snap["system"]["last_frame_hex"],
        last_frame_ms        = int(snap["system"].get("last_frame_ts", 0) * 1000),
    )


def start() -> None:
    """启动串口采集、API 服务及所有后台线程。由 main.py 在后台线程调用。"""
    if MOCK_MODE:
        return
    if _remote_url_from_env():
        return
    UART_2.start()


_start_error: str = ""


def start_safe() -> None:
    """启动串口，失败时将错误信息写入 _start_error 供 UI 查询。"""
    global _start_error, _remote_runtime_enabled
    if MOCK_MODE:
        _start_error = ""
        print("RADAR_MOCK=1：使用上位机模拟数据")
        return
    if _remote_url_from_env():
        _start_error = ""
        _remote_runtime_enabled = True
        threading.Thread(target=_remote_poll_loop, args=(_remote_url_from_env(),), daemon=True).start()
        print(f"RADAR_REMOTE_URL={_remote_url_from_env()}：使用远端雷达数据")
        return
    try:
        UART_2.start()
    except Exception as e:
        _start_error = str(e)
        print("雷达启动失败:", e)


def get_start_error() -> str:
    """返回启动错误信息，正常则返回空字符串。"""
    return _start_error
