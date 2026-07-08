# =========================================================
# R60ABD1 雷达完整监测程序（RK3588 / Forlinx ELF2）
# 重点：不改动原有串口获取命令，只增加“数据处理层 + 前端 API 层”
# =========================================================

import os
import serial
import time
import threading
import copy
from fastapi import FastAPI
import uvicorn

# =========================================================
# 串口配置
# =========================================================
# 串口参数允许板端用环境变量覆盖，方便 RK3576 从机和飞凌 RK3588 使用不同设备名。
PORT = os.environ.get("RADAR_PORT", "/dev/ttyS9")
BAUD = int(os.environ.get("RADAR_BAUD", "115200"))

# 波形数据保留长度：雷达波形每秒 5 个点，300 点约等于 60 秒
WAVE_MAX_LEN = 300
WAVE_SAMPLE_INTERVAL_MS = 200

# 前端判断数据是否过期用，不影响底层采集
STALE_SECONDS = {
    "human.exist": 45,        # 有人->无人约 40s 上报，给 45s 余量
    "human.motion_state": 5,
    "human.motion_val": 5,
    "human.distance": 5,
    "human.position": 5,
    "heart.rate": 6,          # 心率 3s 一次
    "heart.wave": 3,          # 波形查询 1s 一次
    "breath.rate": 6,         # 呼吸 3s 一次
    "breath.state": 45,
    "breath.wave": 3,
    "sleep.realtime": 660,    # 睡眠状态 10min 一次，给 11min 余量
}

# =========================================================
# 前端数据总缓存：保留原始数值，供调试 / 兼容旧前端使用
# =========================================================
data_store = {
    "human": {
        "exist": 0,           # 0无人, 1有人
        "motion_state": 0,    # 0无/无人, 1静止, 2活跃
        "motion_val": 0,      # 体动参数 0~100
        "distance": 0,        # 距离 cm
        "x": 0,               # 方位 X cm
        "y": 0,               # 方位 Y cm
        "z": 0,               # 方位 Z cm
    },
    "heart": {
        "rate": 0,            # 心率 bpm
        "wave": []            # 心率波形，已转换为以 0 为中线：原始值 - 128
    },
    "breath": {
        "rate": 0,            # 呼吸频率 次/min
        "state": 0,           # 1正常, 2过高, 3过低, 4无呼吸
        "wave": []            # 呼吸波形，已转换为以 0 为中线：原始值 - 128
    },
    "sleep": {
        "bed": 0,             # 0离床, 1入床, 2无
        "state": 3,           # 0深睡, 1浅睡, 2清醒, 3无人/无
        "awake_time": 0,      # 清醒时长 分钟
        "light_time": 0,      # 浅睡时长 分钟
        "deep_time": 0,       # 深睡时长 分钟
        "score": 0,           # 睡眠评分 0~100
        "avg_breath": 0,      # 平均呼吸
        "avg_heart": 0,       # 平均心跳
        "turn_over": 0,       # 翻身次数
        "big_motion_ratio": 0,# 大幅度体动占比 0~100
        "small_motion_ratio": 0,# 小幅度体动占比 0~100
        "apnea": 0,           # 呼吸暂停次数
        "total_sleep": 0,     # 总睡眠时长 分钟
        "awake_ratio": 0,     # 清醒占比 0~100
        "light_ratio": 0,     # 浅睡占比 0~100
        "deep_ratio": 0,      # 深睡占比 0~100
        "out_bed_time": 0,    # 离床时长/离床占比，按协议原始值保存
        "out_bed_count": 0,   # 离床次数
        "exception": 0,       # 睡眠异常状态
        "rating": 0,          # 睡眠评级
        "struggle": 0,        # 异常挣扎
        "nobody_timer": 0     # 无人计时
    },
    "system": {
        "last_frame_ts": 0,
        "last_frame_hex": "",
        "frame_count": 0,
        "checksum_error_count": 0,
        "parse_error_count": 0
    }
}

# 每个字段的更新时间戳，供前端显示“数据是否新鲜”
field_ts = {}

# 线程锁：串口线程写入与前端读取时防冲突
data_lock = threading.Lock()

# 串口写锁：防止多线程并发写导致帧交错
_write_lock = threading.Lock()

# =========================================================
# 枚举文字映射：前端可直接显示 label，也可根据 code 自己渲染
# =========================================================
HUMAN_EXIST_TEXT = {0: "无人", 1: "有人"}
MOVE_STATE_TEXT = {0: "无人", 1: "静止", 2: "活跃"}
BREATH_STATE_TEXT = {1: "正常", 2: "呼吸过高", 3: "呼吸过低", 4: "无呼吸"}
BED_STATE_TEXT = {0: "离床", 1: "入床", 2: "无"}
SLEEP_STATE_TEXT = {0: "深睡", 1: "浅睡", 2: "清醒", 3: "无人"}
SLEEP_EXCEPTION_TEXT = {0: "睡眠不足4小时", 1: "睡眠超过12小时", 2: "长时间异常无人", 3: "无异常"}
SLEEP_RATING_TEXT = {0: "无", 1: "良好", 2: "一般", 3: "较差"}
STRUGGLE_TEXT = {0: "无", 1: "正常", 2: "异常挣扎"}
NOBODY_TIMER_TEXT = {0: "无", 1: "正常", 2: "异常"}

# =========================================================
# 数据更新接口：只做赋值与时间戳，不改变串口获取逻辑
# =========================================================
def _now():
    return time.time()


def update_data(path, value):
    """path: ["human", "exist"] 这种形式"""
    ts = _now()
    key = ".".join(path)
    with data_lock:
        ref = data_store
        for p in path[:-1]:
            ref = ref[p]
        ref[path[-1]] = value
        field_ts[key] = ts


def update_many(updates):
    """一次更新多个字段，减少锁粒度。updates: [([path], value), ...]"""
    ts = _now()
    with data_lock:
        for path, value in updates:
            ref = data_store
            for p in path[:-1]:
                ref = ref[p]
            ref[path[-1]] = value
            field_ts[".".join(path)] = ts


def update_wave(path, wave, max_len=WAVE_MAX_LEN):
    """波形以 v - 128 后的值缓存，便于前端以 0 为中线画曲线。"""
    ts = _now()
    key = ".".join(path)
    with data_lock:
        ref = data_store
        for p in path:
            ref = ref[p]
        ref.extend(wave)
        if len(ref) > max_len:
            del ref[:-max_len]
        field_ts[key] = ts


def update_system_last_frame(raw):
    ts = _now()
    with data_lock:
        data_store["system"]["last_frame_ts"] = ts
        data_store["system"]["last_frame_hex"] = raw.hex(" ").upper()
        data_store["system"]["frame_count"] += 1
        field_ts["system.last_frame"] = ts


def inc_system_counter(name):
    with data_lock:
        data_store["system"][name] += 1

# =========================================================
# 前端数据处理层
# =========================================================
def percent(value):
    try:
        return max(0, min(100, int(value)))
    except Exception:
        return 0


def is_stale(key, max_age):
    ts = field_ts.get(key, 0)
    if ts <= 0:
        return True
    return (_now() - ts) > max_age


def last_update_ms(key):
    ts = field_ts.get(key, 0)
    return int(ts * 1000) if ts else 0


def status_obj(code, text_map, key=None, stale_key=None):
    label = text_map.get(code, "未知")
    result = {
        "code": code,
        "label": label
    }
    if key:
        result["last_update_ms"] = last_update_ms(key)
    if stale_key:
        result["stale"] = is_stale(stale_key, STALE_SECONDS.get(stale_key, 10))
    return result


def value_obj(value, unit="", key=None, stale_key=None, min_value=None, max_value=None):
    result = {
        "value": value,
        "unit": unit
    }
    if min_value is not None:
        result["min"] = min_value
    if max_value is not None:
        result["max"] = max_value
    if key:
        result["last_update_ms"] = last_update_ms(key)
    if stale_key:
        result["stale"] = is_stale(stale_key, STALE_SECONDS.get(stale_key, 10))
    return result


def wave_to_points(wave):
    """前端图表可直接使用：x 为点序号，y 为中线归一后的值。"""
    start_index = max(0, len(wave) - WAVE_MAX_LEN)
    return [{"x": start_index + i, "y": v} for i, v in enumerate(wave)]


def calc_sleep_duration_text(total_minutes):
    hours = int(total_minutes) // 60
    minutes = int(total_minutes) % 60
    return f"{hours}小时{minutes}分钟"


def build_frontend_data(snapshot, ts_snapshot):
    """
    把原始 code 转为前端可直接显示的数据结构。

    注意：这里是“前端显示逻辑层”，不改变串口获取、不改变原始解析、不改变 /radar/raw。

    显示规则：
    1. 有/无人状态不跟心率、呼吸变化走；心率、呼吸只作为生命体征显示。
    2. 无人时，运动状态显示“无人”。
    3. 有人且雷达上报静止/活跃时，显示“静止/活跃”。
    4. 有人但运动状态原始值为 0 时，前端显示“静止”。
    5. 有人但还没有判断出深睡/浅睡时，先显示“清醒”。
    6. 深睡/浅睡/清醒时，必须显示“有人 + 入床”。
    7. 无人时，睡眠状态显示“无人”，入/离床显示“离床”。
    """
    h = snapshot["human"]
    heart = snapshot["heart"]
    b = snapshot["breath"]
    s = snapshot["sleep"]
    sys = snapshot["system"]

    heart_rate = int(heart.get("rate", 0) or 0)
    breath_rate = int(b.get("rate", 0) or 0)
    sleep_total = int(s.get("total_sleep", 0) or 0)

    # ---------------- 前端显示逻辑：只生成 display 值，不改原始数据 ----------------
    raw_exist = int(h.get("exist", 0) or 0)
    raw_motion = int(h.get("motion_state", 0) or 0)
    raw_bed = int(s.get("bed", 0) or 0)
    raw_sleep_state = int(s.get("state", 3) or 0)

    sleep_state_received = ts_snapshot.get("sleep.state", 0) > 0
    bed_state_received = ts_snapshot.get("sleep.bed", 0) > 0

    # 睡眠状态 0/1/2 表示深睡/浅睡/清醒，生活逻辑上一定有人且入床。
    sleep_means_person_in_bed = sleep_state_received and raw_sleep_state in (0, 1, 2)

    # 有/无人显示：不使用心率、呼吸判断。
    # 只使用人体存在原始状态，以及睡眠状态 0/1/2 的生活逻辑修正。
    display_exist = 1 if (raw_exist == 1 or sleep_means_person_in_bed) else 0

    # 运动状态显示：
    # 无人 -> 无人；有人 + 静止/活跃 -> 原样；有人 + 原始0 -> 静止。
    if display_exist == 0:
        display_motion_state = 0
    else:
        display_motion_state = raw_motion if raw_motion in (1, 2) else 1

    # 睡眠状态显示：
    # 深睡/浅睡/清醒 -> 原样；有人但未判断睡眠阶段 -> 清醒；无人 -> 无人。
    if sleep_means_person_in_bed:
        display_sleep_state = raw_sleep_state
    elif display_exist == 1:
        display_sleep_state = 2
    else:
        display_sleep_state = 3

    # 入/离床显示：
    # 有人或深睡/浅睡/清醒 -> 入床；无人 -> 离床。
    if display_exist == 1 or display_sleep_state in (0, 1, 2):
        display_bed = 1
    elif bed_state_received and raw_bed in (0, 2):
        display_bed = 0
    else:
        display_bed = 0

    # 用于 /radar 的显示对象；raw 保留在 /radar/raw 中。
    display_human = dict(h)
    display_sleep = dict(s)
    display_human["exist"] = display_exist
    display_human["motion_state"] = display_motion_state
    display_sleep["bed"] = display_bed
    display_sleep["state"] = display_sleep_state

    alerts = []
    if display_exist == 0:
        alerts.append({"level": "info", "message": "无人"})
    if b.get("state", 0) in (2, 3, 4):
        alerts.append({"level": "warning", "message": BREATH_STATE_TEXT.get(b.get("state"), "呼吸状态异常")})
    if s.get("exception", 3) != 3:
        alerts.append({"level": "warning", "message": SLEEP_EXCEPTION_TEXT.get(s.get("exception"), "睡眠异常")})
    if s.get("struggle", 0) == 2:
        alerts.append({"level": "warning", "message": "异常挣扎状态"})
    if s.get("nobody_timer", 0) == 2:
        alerts.append({"level": "warning", "message": "无人计时异常"})

    return {
        "timestamp_ms": int(_now() * 1000),
        "device": {
            "name": "R60ABD1 呼吸睡眠雷达",
            "board": "RK3588 Forlinx ELF2",
            "api_version": "processed-display-v3"
        },
        "summary_cards": [
            {
                "id": "human_exist",
                "title": "人体存在",
                "value": HUMAN_EXIST_TEXT.get(display_human.get("exist"), "未知"),
                "code": display_human.get("exist")
            },
            {
                "id": "motion_state",
                "title": "运动状态",
                "value": MOVE_STATE_TEXT.get(display_human.get("motion_state"), "未知"),
                "code": display_human.get("motion_state")
            },
            {
                "id": "heart_rate",
                "title": "心率",
                "value": heart_rate,
                "unit": "bpm"
            },
            {
                "id": "breath_rate",
                "title": "呼吸",
                "value": breath_rate,
                "unit": "次/min"
            },
            {
                "id": "sleep_state",
                "title": "睡眠状态",
                "value": SLEEP_STATE_TEXT.get(display_sleep.get("state"), "未知"),
                "code": display_sleep.get("state")
            }
        ],
        "human": {
            "exist": status_obj(display_human.get("exist", 0), HUMAN_EXIST_TEXT, "human.exist"),
            "motion_state": status_obj(display_human.get("motion_state", 0), MOVE_STATE_TEXT, "human.motion_state"),
            "motion_value": value_obj(display_human.get("motion_val", 0), "%", "human.motion_val", None, 0, 100),
            "distance": value_obj(display_human.get("distance", 0), "cm", "human.distance", None, 0, 65535),
            "position": {
                "x": value_obj(display_human.get("x", 0), "cm"),
                "y": value_obj(display_human.get("y", 0), "cm"),
                "z": value_obj(display_human.get("z", 0), "cm"),
                "last_update_ms": max(
                    ts_snapshot.get("human.x", 0),
                    ts_snapshot.get("human.y", 0),
                    ts_snapshot.get("human.z", 0)
                )
            }
        },
        "heart": {
            "rate": value_obj(heart_rate, "bpm", "heart.rate", None, 0, 150),
            "wave": {
                "sample_interval_ms": WAVE_SAMPLE_INTERVAL_MS,
                "center_line": 0,
                "source_center_line": 128,
                "values": heart.get("wave", []),
                "points": wave_to_points(heart.get("wave", [])),
                "last_update_ms": last_update_ms("heart.wave")
            }
        },
        "breath": {
            "rate": value_obj(breath_rate, "次/min", "breath.rate", None, 0, 35),
            "state": status_obj(b.get("state", 0), BREATH_STATE_TEXT, "breath.state"),
            "wave": {
                "sample_interval_ms": WAVE_SAMPLE_INTERVAL_MS,
                "center_line": 0,
                "source_center_line": 128,
                "values": b.get("wave", []),
                "points": wave_to_points(b.get("wave", [])),
                "last_update_ms": last_update_ms("breath.wave")
            }
        },
        "sleep": {
            "bed": status_obj(display_sleep.get("bed", 0), BED_STATE_TEXT, "sleep.bed"),
            "state": status_obj(display_sleep.get("state", 3), SLEEP_STATE_TEXT, "sleep.state"),
            "duration": {
                "awake_minutes": s.get("awake_time", 0),
                "light_minutes": s.get("light_time", 0),
                "deep_minutes": s.get("deep_time", 0),
                "total_minutes": sleep_total,
                "total_text": calc_sleep_duration_text(sleep_total)
            },
            "quality": {
                "score": value_obj(s.get("score", 0), "分", "sleep.score", None, 0, 100),
                "rating": status_obj(s.get("rating", 0), SLEEP_RATING_TEXT, "sleep.rating"),
                "awake_ratio": value_obj(percent(s.get("awake_ratio", 0)), "%", "sleep.awake_ratio", None, 0, 100),
                "light_ratio": value_obj(percent(s.get("light_ratio", 0)), "%", "sleep.light_ratio", None, 0, 100),
                "deep_ratio": value_obj(percent(s.get("deep_ratio", 0)), "%", "sleep.deep_ratio", None, 0, 100),
            },
            "statistics": {
                "avg_breath": value_obj(s.get("avg_breath", 0), "次/min"),
                "avg_heart": value_obj(s.get("avg_heart", 0), "bpm"),
                "turn_over": value_obj(s.get("turn_over", 0), "次"),
                "big_motion_ratio": value_obj(percent(s.get("big_motion_ratio", 0)), "%"),
                "small_motion_ratio": value_obj(percent(s.get("small_motion_ratio", 0)), "%"),
                "apnea": value_obj(s.get("apnea", 0), "次"),
                "out_bed_time": value_obj(s.get("out_bed_time", 0), ""),
                "out_bed_count": value_obj(s.get("out_bed_count", 0), "次")
            },
            "exception": status_obj(s.get("exception", 3), SLEEP_EXCEPTION_TEXT, "sleep.exception"),
            "struggle": status_obj(s.get("struggle", 0), STRUGGLE_TEXT, "sleep.struggle"),
            "nobody_timer": status_obj(s.get("nobody_timer", 0), NOBODY_TIMER_TEXT, "sleep.nobody_timer")
        },
        "display_logic": {
            "exist_not_from_heart_breath": True,
            "heart_breath_only_vital_signs": True,
            "raw_exist": raw_exist,
            "raw_motion_state": raw_motion,
            "raw_sleep_state": raw_sleep_state,
            "sleep_state_received": sleep_state_received,
            "display_exist": display_exist,
            "display_motion_state": display_motion_state,
            "display_sleep_state": display_sleep_state,
            "display_bed": display_bed
        },
        "alerts": alerts,
        "system": {
            "last_frame_ms": int(sys.get("last_frame_ts", 0) * 1000),
            "frame_count": sys.get("frame_count", 0),
            "checksum_error_count": sys.get("checksum_error_count", 0),
            "parse_error_count": sys.get("parse_error_count", 0)
        }
    }


def get_snapshot():
    with data_lock:
        return copy.deepcopy(data_store), copy.deepcopy(field_ts)

# =========================================================
# FastAPI 接口设置
# =========================================================
app = FastAPI(title="R60ABD1 Radar API")

@app.get("/radar")
def get_radar_processed():
    """前端推荐使用：已经处理好的显示数据。"""
    snapshot, ts_snapshot = get_snapshot()
    return build_frontend_data(snapshot, ts_snapshot)

@app.get("/radar/raw")
def get_radar_raw():
    """调试用：保留原始数值缓存。"""
    snapshot, _ = get_snapshot()
    return snapshot

@app.get("/radar/wave")
def get_radar_wave():
    """前端只刷新曲线时可调用这个轻量接口。"""
    snapshot, _ = get_snapshot()
    return {
        "timestamp_ms": int(_now() * 1000),
        "heart": {
            "sample_interval_ms": WAVE_SAMPLE_INTERVAL_MS,
            "values": snapshot["heart"]["wave"],
            "points": wave_to_points(snapshot["heart"]["wave"]),
        },
        "breath": {
            "sample_interval_ms": WAVE_SAMPLE_INTERVAL_MS,
            "values": snapshot["breath"]["wave"],
            "points": wave_to_points(snapshot["breath"]["wave"]),
        }
    }


def start_api():
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")

# =========================================================
# 串口对象：由 start() 初始化
# =========================================================
ser = None

# =========================================================
# 辅助函数：原串口命令生成逻辑保持不变
# =========================================================
def checksum(data):
    return sum(data) & 0xFF


def send_cmd(ctrl, cmd, payload):
    if ser is None:
        return
    length = len(payload)
    frame = [0x53, 0x59, ctrl, cmd, (length >> 8) & 0xFF, length & 0xFF] + payload
    cs = checksum(frame)
    frame += [cs, 0x54, 0x43]
    data = bytes(frame)
    with _write_lock:
        ser.write(data)
    print("\nSEND:", data.hex(" ").upper())


def signed_16(high, low):
    value = (high << 8) | low
    if value & 0x8000:
        value -= 0x10000
    return value


def sleep_state_text(v):
    return SLEEP_STATE_TEXT.get(v, "未知")


def breath_state_text(v):
    return BREATH_STATE_TEXT.get(v, "未知")


def move_state_text(v):
    return MOVE_STATE_TEXT.get(v, "未知")

# =========================================================
# 数据解析：保留原解析分支，只在每个分支后补充前端处理字段
# =========================================================
def parse_frame(data):
    i = 0
    while i < len(data) - 8:
        if data[i] == 0x53 and data[i + 1] == 0x59:
            ctrl = data[i + 2]
            cmd = data[i + 3]
            length = ((data[i + 4] << 8) | data[i + 5])
            start = i + 6
            end = start + length

            if end + 3 > len(data):
                break

            payload = list(data[start:end])
            recv_cs = data[end]
            tail1 = data[end + 1]
            tail2 = data[end + 2]

            if tail1 != 0x54 or tail2 != 0x43:
                i += 1
                continue

            calc_cs = checksum(list(data[i:end]))
            if calc_cs != recv_cs:
                print("校验失败")
                inc_system_counter("checksum_error_count")
                i += 1
                continue

            raw = data[i:end + 3]
            update_system_last_frame(raw)

            print("\nRAW:", raw.hex(" ").upper())
            print("CTRL:", hex(ctrl))
            print("CMD :", hex(cmd))
            print("LEN :", length)
            print("Payload:", payload)

            try:
                # --------------------- 人体存在 ---------------------
                if ctrl == 0x80 and cmd == 0x01 and length >= 1:
                    print("\n========== 人体存在 ==========")
                    print("状态:", HUMAN_EXIST_TEXT.get(payload[0], "未知"))
                    update_data(["human", "exist"], payload[0])

                # --------------------- 运动状态 ---------------------
                elif ctrl == 0x80 and cmd == 0x02 and length >= 1:
                    print("\n========== 运动状态 ==========")
                    print("状态:", move_state_text(payload[0]))
                    update_data(["human", "motion_state"], payload[0])

                # --------------------- 体动参数 ---------------------
                elif ctrl == 0x80 and cmd == 0x03 and length >= 1:
                    print("\n========== 体动参数 ==========")
                    print("体动值:", payload[0])
                    update_data(["human", "motion_val"], percent(payload[0]))

                # --------------------- 人体距离 ---------------------
                elif ctrl == 0x80 and cmd == 0x04 and length >= 2:
                    print("\n========== 人体距离 ==========")
                    distance = ((payload[0] << 8) | payload[1])
                    print(f"距离: {distance} cm")
                    update_data(["human", "distance"], distance)

                # --------------------- 人体方位 ---------------------
                elif ctrl == 0x80 and cmd == 0x05 and length >= 6:
                    print("\n========== 人体方位 ==========")
                    x = signed_16(payload[0], payload[1])
                    y = signed_16(payload[2], payload[3])
                    z = signed_16(payload[4], payload[5])
                    print(f"X: {x} cm")
                    print(f"Y: {y} cm")
                    print(f"Z: {z} cm")
                    update_many([
                        (["human", "x"], x),
                        (["human", "y"], y),
                        (["human", "z"], z),
                    ])

                # --------------------- 心率 ---------------------
                elif ctrl == 0x85 and cmd == 0x02 and length >= 1:
                    print("\n========== 心率 ==========")
                    print(f"心率: {payload[0]} bpm")
                    update_data(["heart", "rate"], payload[0])

                # --------------------- 心率波形 ---------------------
                # 自主查询回复为 0x85；若后续改主动上报 0x05，也能兼容处理
                elif ctrl == 0x85 and cmd in (0x05, 0x85) and length >= 1:
                    print("\n========== 心率波形 ==========")
                    print("原始波形:", payload)
                    wave = [v - 128 for v in payload]
                    print("解析波形:", wave)
                    update_wave(["heart", "wave"], wave)

                # --------------------- 呼吸信息 ---------------------
                elif ctrl == 0x81 and cmd == 0x01 and length >= 1:
                    print("\n========== 呼吸信息 ==========")
                    print("状态:", breath_state_text(payload[0]))
                    update_data(["breath", "state"], payload[0])

                # --------------------- 呼吸频率 ---------------------
                elif ctrl == 0x81 and cmd == 0x02 and length >= 1:
                    print("\n========== 呼吸频率 ==========")
                    print(f"呼吸: {payload[0]} 次/min")
                    update_data(["breath", "rate"], payload[0])

                # --------------------- 呼吸波形 ---------------------
                # 自主查询回复为 0x85；若后续改主动上报 0x05，也能兼容处理
                elif ctrl == 0x81 and cmd in (0x05, 0x85) and length >= 1:
                    print("\n========== 呼吸波形 ==========")
                    print("原始波形:", payload)
                    wave = [v - 128 for v in payload]
                    print("解析波形:", wave)
                    update_wave(["breath", "wave"], wave)

                # --------------------- 入床离床 ---------------------
                elif ctrl == 0x84 and cmd == 0x01 and length >= 1:
                    print("\n========== 入床离床 ==========")
                    state = payload[0]
                    print("状态:", BED_STATE_TEXT.get(state, "未知"))
                    update_data(["sleep", "bed"], state)

                # --------------------- 睡眠状态 ---------------------
                elif ctrl == 0x84 and cmd == 0x02 and length >= 1:
                    print("\n========== 睡眠状态 ==========")
                    print("状态:", sleep_state_text(payload[0]))
                    update_data(["sleep", "state"], payload[0])

                # --------------------- 清醒时长 ---------------------
                elif ctrl == 0x84 and cmd == 0x03 and length >= 2:
                    print("\n========== 清醒时长 ==========")
                    value = ((payload[0] << 8) | payload[1])
                    print(f"{value} 分钟")
                    update_data(["sleep", "awake_time"], value)

                # --------------------- 浅睡时长 ---------------------
                elif ctrl == 0x84 and cmd == 0x04 and length >= 2:
                    print("\n========== 浅睡时长 ==========")
                    value = ((payload[0] << 8) | payload[1])
                    print(f"{value} 分钟")
                    update_data(["sleep", "light_time"], value)

                # --------------------- 深睡时长 ---------------------
                elif ctrl == 0x84 and cmd == 0x05 and length >= 2:
                    print("\n========== 深睡时长 ==========")
                    value = ((payload[0] << 8) | payload[1])
                    print(f"{value} 分钟")
                    update_data(["sleep", "deep_time"], value)

                # --------------------- 睡眠评分 ---------------------
                elif ctrl == 0x84 and cmd == 0x06 and length >= 1:
                    print("\n========== 睡眠评分 ==========")
                    print("评分:", payload[0])
                    update_data(["sleep", "score"], percent(payload[0]))

                # --------------------- 睡眠综合状态 ---------------------
                elif ctrl == 0x84 and cmd == 0x0C and length >= 8:
                    print("\n========== 睡眠综合状态 ==========")
                    print("存在:", "有人" if payload[0] else "无人")
                    print("睡眠状态:", sleep_state_text(payload[1]))
                    print("平均呼吸:", payload[2])
                    print("平均心跳:", payload[3])
                    print("翻身次数:", payload[4])
                    print("大体动占比:", payload[5])
                    print("小体动占比:", payload[6])
                    print("呼吸暂停:", payload[7])
                    update_many([
                        (["human", "exist"], payload[0]),
                        (["sleep", "state"], payload[1]),
                        (["sleep", "avg_breath"], payload[2]),
                        (["sleep", "avg_heart"], payload[3]),
                        (["sleep", "turn_over"], payload[4]),
                        (["sleep", "big_motion_ratio"], percent(payload[5])),
                        (["sleep", "small_motion_ratio"], percent(payload[6])),
                        (["sleep", "apnea"], payload[7]),
                    ])

                # --------------------- 睡眠质量分析 ---------------------
                elif ctrl == 0x84 and cmd == 0x0D and length >= 12:
                    print("\n========== 睡眠质量分析 ==========")
                    total_sleep = ((payload[1] << 8) | payload[2])
                    print("睡眠评分:", payload[0])
                    print("总睡眠:", total_sleep)
                    print("清醒占比:", payload[3])
                    print("浅睡占比:", payload[4])
                    print("深睡占比:", payload[5])
                    print("离床时长:", payload[6])
                    print("离床次数:", payload[7])
                    print("翻身次数:", payload[8])
                    print("平均呼吸:", payload[9])
                    print("平均心跳:", payload[10])
                    print("呼吸暂停:", payload[11])
                    update_many([
                        (["sleep", "score"], percent(payload[0])),
                        (["sleep", "total_sleep"], total_sleep),
                        (["sleep", "awake_ratio"], percent(payload[3])),
                        (["sleep", "light_ratio"], percent(payload[4])),
                        (["sleep", "deep_ratio"], percent(payload[5])),
                        (["sleep", "out_bed_time"], payload[6]),
                        (["sleep", "out_bed_count"], payload[7]),
                        (["sleep", "turn_over"], payload[8]),
                        (["sleep", "avg_breath"], payload[9]),
                        (["sleep", "avg_heart"], payload[10]),
                        (["sleep", "apnea"], payload[11]),
                    ])

                # --------------------- 睡眠异常 ---------------------
                elif ctrl == 0x84 and cmd == 0x0E and length >= 1:
                    print("\n========== 睡眠异常 ==========")
                    state = payload[0]
                    print(SLEEP_EXCEPTION_TEXT.get(state, "未知"))
                    update_data(["sleep", "exception"], state)

                # --------------------- 睡眠评级 ---------------------
                elif ctrl == 0x84 and cmd == 0x10 and length >= 1:
                    print("\n========== 睡眠评级 ==========")
                    state = payload[0]
                    print(SLEEP_RATING_TEXT.get(state, "未知"))
                    update_data(["sleep", "rating"], state)

                # --------------------- 异常挣扎 ---------------------
                elif ctrl == 0x84 and cmd == 0x11 and length >= 1:
                    print("\n========== 异常挣扎 ==========")
                    state = payload[0]
                    print(STRUGGLE_TEXT.get(state, "未知"))
                    update_data(["sleep", "struggle"], state)

                # --------------------- 无人计时 ---------------------
                elif ctrl == 0x84 and cmd == 0x12 and length >= 1:
                    print("\n========== 无人计时 ==========")
                    state = payload[0]
                    print(NOBODY_TIMER_TEXT.get(state, "未知"))
                    update_data(["sleep", "nobody_timer"], state)

            except Exception as e:
                inc_system_counter("parse_error_count")
                print("解析字段异常:", e)

            i = end + 3
        else:
            i += 1

# =========================================================
# 波形查询线程：保持原样，只查询心率/呼吸波形
# =========================================================
def heart_wave_query():
    while True:
        try:
            send_cmd(0x85, 0x85, [0x0F])
        except serial.SerialException as e:
            print("心率波形查询串口异常:", e)
        time.sleep(1)


def breath_wave_query():
    while True:
        try:
            send_cmd(0x81, 0x85, [0x0F])
        except serial.SerialException as e:
            print("呼吸波形查询串口异常:", e)
        time.sleep(1)


def _read_loop():
    global ser
    while True:
        try:
            if ser is None or not ser.is_open:
                time.sleep(1)
                continue
            if ser.in_waiting:
                data = ser.read(ser.in_waiting)
                print("\nRECV STREAM:", data.hex(" ").upper())
                parse_frame(data)
            time.sleep(0.05)
        except serial.SerialException as e:
            print("串口断开，3s 后重连:", e)
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(3)
            try:
                ser = serial.Serial(PORT, BAUD, timeout=0.2)
                print("串口重连成功")
            except serial.SerialException as re:
                print("重连失败:", re)
                ser = None
        except Exception as e:
            print("ERROR:", e)
            inc_system_counter("parse_error_count")


# =========================================================
# 启动入口：由 main.py 调用，或直接运行本文件时执行
# =========================================================
def start():
    global ser
    ser = serial.Serial(PORT, BAUD, timeout=0.2)

    print("\n====================")
    print("初始化模块")
    print("====================")

    send_cmd(0x80, 0x00, [0x01])      # 开人体存在功能
    time.sleep(0.5)
    send_cmd(0x85, 0x00, [0x01])      # 开心率监测功能
    time.sleep(0.5)
    send_cmd(0x81, 0x00, [0x01])      # 开呼吸监测功能
    time.sleep(0.5)
    send_cmd(0x81, 0x0B, [10])        # 低缓呼吸判读设置，默认 10 次/min
    time.sleep(0.5)
    send_cmd(0x84, 0x00, [0x01])      # 开睡眠监测功能
    time.sleep(0.5)
    send_cmd(0x84, 0x0F, [0x00])      # 上报模式：实时数据传输
    time.sleep(1)

    threading.Thread(target=heart_wave_query, daemon=True).start()
    threading.Thread(target=breath_wave_query, daemon=True).start()
    threading.Thread(target=start_api, daemon=True).start()
    threading.Thread(target=_read_loop, daemon=True).start()

    print("\nAPI 服务已启动：")
    print("  前端处理数据: http://<设备IP>:8000/radar")
    print("  原始缓存数据: http://<设备IP>:8000/radar/raw")
    print("  波形轻量接口: http://<设备IP>:8000/radar/wave")
    print("\n等待数据...\n")


if __name__ == "__main__":
    start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n程序结束")
