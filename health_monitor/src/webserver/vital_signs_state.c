#include "vital_signs_state.h"
#include <string.h>
#include <time.h>
#include <stdio.h>

/* 全局单例 */
VitalSignsState g_vital_state;
pthread_spinlock_t g_vital_lock;

/* 内部：获取单调时间（毫秒） */
static uint64_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000ULL
         + (uint64_t)ts.tv_nsec / 1000000ULL;
}

void vital_signs_state_init(void)
{
    memset(&g_vital_state, 0, sizeof(g_vital_state));
    pthread_spin_init(&g_vital_lock, PTHREAD_PROCESS_PRIVATE);

    /* 默认值 */
    g_vital_state.heart_rate      = 0.0;
    g_vital_state.breath_rate     = 0.0;
    g_vital_state.presence_state  = PRESENCE_NONE;
    g_vital_state.heart_wave_pos  = 0;
    g_vital_state.breath_wave_pos = 0;
    g_vital_state.timestamp_ms    = now_ms();
}

void vital_signs_update_radar(double hr, double br,
                              uint8_t motion, uint8_t presence,
                              const float *heart_wave, int hw_len,
                              const float *breath_wave, int bw_len)
{
    vital_signs_lock();

    /* frame_count 由 main.c 从雷达驱动同步，此处不自动递增 */
    g_vital_state.heart_rate       = hr;
    g_vital_state.breath_rate      = br;
    g_vital_state.motion_intensity = motion;
    g_vital_state.presence_state   = presence;

    /* 写入心电波环形缓冲区（独立位置） */
    if (heart_wave && hw_len > 0) {
        for (int i = 0; i < hw_len && i < VITAL_WAVE_POINTS; i++) {
            g_vital_state.heart_wave[g_vital_state.heart_wave_pos] = heart_wave[i];
            g_vital_state.heart_wave_pos = (g_vital_state.heart_wave_pos + 1) % VITAL_WAVE_POINTS;
        }
    }

    /* 写入呼吸波环形缓冲区（独立位置） */
    if (breath_wave && bw_len > 0) {
        for (int i = 0; i < bw_len && i < VITAL_WAVE_POINTS; i++) {
            g_vital_state.breath_wave[g_vital_state.breath_wave_pos] = breath_wave[i];
            g_vital_state.breath_wave_pos = (g_vital_state.breath_wave_pos + 1) % VITAL_WAVE_POINTS;
        }
    }

    /* 自动线性化：供 webserver 序列化时以正确时序读取 */
    vital_signs_linearize_waves();

    vital_signs_unlock();
}

void vital_signs_update_vision(uint8_t occupied, float conf,
                               uint8_t fall, float fall_conf)
{
    vital_signs_lock();
    g_vital_state.bed_occupied   = occupied;
    g_vital_state.bed_confidence = conf;
    g_vital_state.fall_detected  = fall;
    g_vital_state.fall_confidence = fall_conf;
    vital_signs_unlock();
}

/* 获取当前时间字符串 "HH:MM" */
static void get_time_str(char *buf, size_t cap)
{
    time_t t = time(NULL);
    struct tm *tm = localtime(&t);
    if (tm) {
        strftime(buf, cap, "%H:%M", tm);
    } else {
        snprintf(buf, cap, "--:--");
    }
}

void vital_signs_add_alarm(const char *title, const char *detail, const char *level)
{
    vital_signs_lock();

    /* 去重：检查是否已存在相同标题的告警 */
    for (int i = 0; i < g_vital_state.alarm_count && i < VITAL_MAX_ALARMS; i++) {
        if (strncmp(g_vital_state.alarms[i].title, title, VITAL_ALARM_LEN - 1) == 0) {
            /* 已存在，更新时间戳 */
            get_time_str(g_vital_state.alarms[i].time, sizeof(g_vital_state.alarms[i].time));
            vital_signs_unlock();
            return;
        }
    }

    /* 循环覆盖 */
    int idx = g_vital_state.alarm_count % VITAL_MAX_ALARMS;
    VitalAlarm *a = &g_vital_state.alarms[idx];

    snprintf(a->level, sizeof(a->level), "%s", level ? level : "warn");
    snprintf(a->title, sizeof(a->title), "%s", title ? title : "");
    get_time_str(a->time, sizeof(a->time));
    snprintf(a->detail, sizeof(a->detail), "%s", detail ? detail : "");

    if (g_vital_state.alarm_count < VITAL_MAX_ALARMS) {
        g_vital_state.alarm_count++;
    }

    vital_signs_unlock();
}

void vital_signs_linearize_waves(void)
{
    /* 心电波：从环形缓冲区按时间顺序导出到 heart_linear */
    uint16_t pos = g_vital_state.heart_wave_pos;
    for (int i = 0; i < VITAL_WAVE_POINTS; i++) {
        g_vital_state.heart_linear[i] =
            g_vital_state.heart_wave[(pos + i) % VITAL_WAVE_POINTS];
    }

    /* 呼吸波 */
    pos = g_vital_state.breath_wave_pos;
    for (int i = 0; i < VITAL_WAVE_POINTS; i++) {
        g_vital_state.breath_linear[i] =
            g_vital_state.breath_wave[(pos + i) % VITAL_WAVE_POINTS];
    }
}

void vital_signs_tick(void)
{
    vital_signs_lock();
    uint64_t now = now_ms();
    g_vital_state.timestamp_ms = now;
    g_vital_state.uptime_sec   = now / 1000ULL;
    vital_signs_unlock();
}
