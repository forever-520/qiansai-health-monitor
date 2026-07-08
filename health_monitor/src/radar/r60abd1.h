/*
 * r60abd1.h — R60ABD1 毫米波雷达 C 驱动 (RK3588 Linux)
 *
 * 协议：自定义串口协议，帧格式：
 *   HEAD(2) | CONTROL(1) | COMMAND(1) | LEN(2) | DATA(LEN) | CHECKSUM(1) | TAIL(2)
 *   0x53 0x59                                            SUM         0x54 0x43
 *
 * 帧类型：
 *   0x80/0x01 — 配置/心跳
 *   0x80/0x03 — 运动强度 (1 byte, 0~100)
 *   0x85/0x02 — 心率 (1 byte, bpm)
 *   0x81/0x02 — 呼吸率 (1 byte, bpm)
 *   0x85/0x05 — 心电波形 (N bytes, 每个采样点偏置 128)
 *   0x81/0x05 — 呼吸波形 (N bytes)
 *
 * 集成到 health_monitor 项目使用：
 *   1. r60abd1_init() 初始化
 *   2. 每次收到 UART 字节，调用 r60abd1_feed()
 *   3. feed 返回 R60_OK 时，读取 r60abd1_get_last_frame() 获得解析结果
 *   4. 主循环用 r60abd1_get_heartrate() / get_breathrate() 等获取最新值
 */

#ifndef R60ABD1_H
#define R60ABD1_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ===== 帧类型常量 ===== */
#define R60_HEAD1         0x53
#define R60_HEAD2         0x59
#define R60_TAIL1         0x54
#define R60_TAIL2         0x43

#define R60_CTRL_CFG      0x80
#define R60_CTRL_BREATH   0x81
#define R60_CTRL_HEART    0x85

#define R60_CMD_HEARTBEAT  0x01
#define R60_CMD_MOTION     0x03
#define R60_CMD_RATE       0x02
#define R60_CMD_WAVEFORM   0x05

/* 波形缓冲区长度 */
#define R60_WAVE_BUF       512

/* ===== 状态码 ===== */
typedef enum {
    R60_OK           = 0,    /* 成功收到一帧 */
    R60_AGAIN        = 1,    /* 帧数据不完整，继续等待 */
    R60_ERR_HEAD     = -1,   /* 帧头不匹配 */
    R60_ERR_TAIL     = -2,   /* 帧尾不匹配 */
    R60_ERR_LENGTH   = -3,   /* 长度字段与实际不符 */
    R60_ERR_CHECKSUM = -4,   /* 校验和错误 */
    R60_ERR_NOMEM    = -5,   /* 内部缓冲区溢出 */
} r60_status_t;

/* ===== 解析后的帧结构 ===== */
typedef struct {
    uint8_t  control;
    uint8_t  command;
    uint16_t length;
    uint8_t  data[256];      /* 最大有效载荷 */
    uint16_t raw_len;
    uint8_t  raw[264];       /* 完整原始帧 */
} r60_frame_t;

/* ===== 驱动句柄（不透明指针） ===== */
typedef struct r60_context r60_context_t;

/* ===== API ===== */

/* 分配并初始化驱动上下文。返回 NULL 表示分配失败。 */
r60_context_t *r60abd1_init(void);

/* 释放驱动上下文。 */
void r60abd1_free(r60_context_t *ctx);

/* 重置解析状态（例如串口断线后重连时调用）。 */
void r60abd1_reset(r60_context_t *ctx);

/* 喂一个字节给状态机。R60_OK 表示刚完成一帧解析。 */
r60_status_t r60abd1_feed(r60_context_t *ctx, uint8_t byte);

/* 获取最近解析完成的帧。仅在 feed 返回 R60_OK 时有效。 */
const r60_frame_t *r60abd1_get_last_frame(const r60_context_t *ctx);

/* ===== 便捷提取函数 ===== */
int  r60abd1_get_heartrate(const r60_context_t *ctx);
int  r60abd1_get_breathrate(const r60_context_t *ctx);
int  r60abd1_get_motion(const r60_context_t *ctx);
const int16_t *r60abd1_get_heartwave(const r60_context_t *ctx, size_t *count);
const int16_t *r60abd1_get_breathwave(const r60_context_t *ctx, size_t *count);
uint64_t r60abd1_get_frame_count(const r60_context_t *ctx);
uint64_t r60abd1_get_parse_errors(const r60_context_t *ctx);
uint64_t r60abd1_get_crc_errors(const r60_context_t *ctx);

/* ===== 构建下行帧（用于向雷达发送配置指令） ===== */
typedef struct {
    uint8_t raw[264];
    uint16_t len;
} r60_tx_frame_t;

/* 构建一帧下行数据。返回的 len 字段指示有效字节数。 */
void r60abd1_build_frame(r60_tx_frame_t *out, uint8_t control,
                         uint8_t command, const uint8_t *data, uint16_t data_len);

#ifdef __cplusplus
}
#endif

#endif /* R60ABD1_H */
