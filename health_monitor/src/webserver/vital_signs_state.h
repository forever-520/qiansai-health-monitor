#ifndef VITAL_SIGNS_STATE_H
#define VITAL_SIGNS_STATE_H

#include <stdint.h>
#include <pthread.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ */
/*  共享状态结构体 —— 雷达驱动、AI 推理、WebSocket 广播三方的数据枢纽   */
/* ------------------------------------------------------------------ */

#define VITAL_WAVE_POINTS 512   /* 波形缓冲区长度 */
#define VITAL_MAX_ALARMS  8     /* 最多保留的告警数 */
#define VITAL_ALARM_LEN   64    /* 单条告警字段最大长度 */

/* 结构化告警（与前端 alarm-list 格式对齐） */
typedef struct {
    char level[8];                /* "warn", "normal", "info" */
    char title[VITAL_ALARM_LEN];  /* 告警标题 */
    char time[12];                /* "HH:MM" */
    char detail[VITAL_ALARM_LEN]; /* 详细描述 */
} VitalAlarm;

/* 在床状态 */
typedef enum {
    PRESENCE_NONE    = 0,  /* 无人 */
    PRESENCE_STILL   = 1,  /* 有人 / 静止 */
    PRESENCE_MOVING  = 2   /* 有人 / 体动 */
} PresenceState;

/* 全局运行时状态 */
typedef struct {
    /* ---------- 雷达数据 ---------- */
    uint32_t frame_count;        /* 已处理有效帧数 */
    uint32_t crc_errors;         /* CRC 校验错误计数 */
    uint32_t parser_errors;      /* 帧解析错误计数 */
    double   heart_rate;         /* 心率 (bpm), 0.1 精度 */
    double   breath_rate;        /* 呼吸率 (rpm), 0.1 精度 */
    uint8_t  motion_intensity;   /* 体动强度 0-100 */
    uint8_t  presence_state;     /* PresenceState 枚举值 */

    /* 波形环形缓冲区（最近 VITAL_WAVE_POINTS 个采样点）
     * 每个波形独立维护环形位置，支持非对称写入长度
     * 线性化后在 webserver 序列化时使用
     */
    float    heart_wave[VITAL_WAVE_POINTS];
    float    breath_wave[VITAL_WAVE_POINTS];
    uint16_t heart_wave_pos;     /* 心电波环形缓冲区下一个写入位置 */
    uint16_t breath_wave_pos;    /* 呼吸波环形缓冲区下一个写入位置 */

    /* ---------- AI 视觉数据 ---------- */
    uint8_t  bed_occupied;       /* 0 = 无人, 1 = 有人 */
    float    bed_confidence;     /* 置信度 0.0-1.0 */
    uint8_t  fall_detected;      /* 0 = 无跌倒, 1 = 跌倒 */
    float    fall_confidence;

    /* ---------- 告警 ---------- */
    uint8_t  alarm_count;
    VitalAlarm alarms[VITAL_MAX_ALARMS];

    /* ---------- 系统 ---------- */
    uint64_t timestamp_ms;       /* 系统启动后毫秒数 */
    uint64_t uptime_sec;         /* 系统启动后秒数 */

    /* 线性化辅助：用于波形序列化时从环形缓冲区读取 */
    float    heart_linear[VITAL_WAVE_POINTS];
    float    breath_linear[VITAL_WAVE_POINTS];
} VitalSignsState;

/* 全局单例 */
extern VitalSignsState g_vital_state;
extern pthread_spinlock_t g_vital_lock;

/* ------------------------------------------------------------------ */
/*  接口函数                                                           */
/* ------------------------------------------------------------------ */

/** 初始化全局状态（清零 + 设默认值） */
void vital_signs_state_init(void);

/** 加锁 / 解锁（封装 spinlock，便于替换为 mutex） */
static inline void vital_signs_lock(void)   { pthread_spin_lock(&g_vital_lock); }
static inline void vital_signs_unlock(void) { pthread_spin_unlock(&g_vital_lock); }

/** 雷达数据更新 */
void vital_signs_update_radar(double hr, double br,
                              uint8_t motion, uint8_t presence,
                              const float *heart_wave, int hw_len,
                              const float *breath_wave, int bw_len);

/** AI 视觉数据更新 */
void vital_signs_update_vision(uint8_t occupied, float conf,
                               uint8_t fall, float fall_conf);

/** 追加一条告警（自动去重 + 循环覆盖） */
/** 追加一条告警
 *  @param title  告警标题（"心率偏高"）
 *  @param detail 详细描述（"103 bpm，超出阈值"）
 *  @param level  严重级别（"warn" / "normal" / "info"）
 */
void vital_signs_add_alarm(const char *title, const char *detail, const char *level);

/** 从环形缓冲区线性化波形数据到 heart_linear / breath_linear */
void vital_signs_linearize_waves(void);

/** 推进时间戳（主循环每秒调用一次） */
void vital_signs_tick(void);

#ifdef __cplusplus
}
#endif

#endif /* VITAL_SIGNS_STATE_H */
