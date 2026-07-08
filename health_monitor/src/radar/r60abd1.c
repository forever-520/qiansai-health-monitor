/*
 * r60abd1.c — R60ABD1 毫米波雷达 C 驱动实现
 *
 * 实现方式：
 *   - 四状态有限状态机（SYNC→HEAD→LEN→DATA→CHECK→TAIL）解析字节流
 *   - 每个帧类型有独立的便捷提取函数
 *   - 波形数据通过循环缓冲区累积，最多保留 R60_WAVE_BUF 个采样点
 *   - 所有 API 不依赖动态内存分配以外的外部库（纯 POSIX）
 */

#include "r60abd1.h"

#include <stdlib.h>
#include <string.h>

/* ===== 状态机状态 ===== */
typedef enum {
    ST_SYNC,     /* 等待 0x53 帧头 */
    ST_HEAD2,    /* 等待 0x59 */
    ST_CONTROL,  /* 读取 control */
    ST_COMMAND,  /* 读取 command */
    ST_LEN_HI,   /* 读取长度高字节 */
    ST_LEN_LO,   /* 读取长度低字节 */
    ST_DATA,     /* 读取有效载荷 */
    ST_CHECK,    /* 读取校验和 */
    ST_TAIL1,    /* 等待 0x54 帧尾 */
} r60_state_t;

/* ===== 上下文结构体（对外部透明） ===== */
struct r60_context {
    r60_state_t  state;
    r60_frame_t  frame;
    r60_frame_t  last_frame;
    uint16_t     data_pos;

    /* 统计 */
    uint64_t     frame_count;
    uint64_t     parse_errors;
    uint64_t     crc_errors;

    /* 便捷提取缓存 */
    int          heartrate;
    int          breathrate;
    int          motion;

    /* 波形环形缓冲区 */
    int16_t      heartwave[R60_WAVE_BUF];
    size_t       heartwave_count;
    int16_t      breathwave[R60_WAVE_BUF];
    size_t       breathwave_count;
};

/* ===== 静态辅助函数 ===== */

static inline uint8_t calc_sum(const uint8_t *buf, size_t len)
{
    unsigned sum = 0;
    for (size_t i = 0; i < len; i++) {
        sum += buf[i];
    }
    return (uint8_t)(sum & 0xFF);
}

static void append_wave(int16_t *buf, size_t *count, size_t max,
                         const uint8_t *data, uint16_t len)
{
    for (uint16_t i = 0; i < len; i++) {
        if (*count >= max) {
            /* 移除最早的一半，腾出空间 */
            size_t half = max / 2;
            memmove(buf, buf + half, (max - half) * sizeof(int16_t));
            *count = max - half;
        }
        buf[(*count)++] = (int16_t)((int)data[i] - 128);
    }
}

/* ===== API 实现 ===== */

r60_context_t *r60abd1_init(void)
{
    r60_context_t *ctx = (r60_context_t *)calloc(1, sizeof(r60_context_t));
    if (!ctx) return NULL;
    ctx->state = ST_SYNC;
    ctx->heartrate = -1;
    ctx->breathrate = -1;
    ctx->motion = -1;
    return ctx;
}

void r60abd1_free(r60_context_t *ctx)
{
    free(ctx);
}

void r60abd1_reset(r60_context_t *ctx)
{
    ctx->state = ST_SYNC;
    ctx->data_pos = 0;
    memset(&ctx->frame, 0, sizeof(ctx->frame));
}

/* ===== 内部尾部分阶段状态 ===== */
#define TAIL_WAIT_54  0
#define TAIL_WAIT_43  1

static void commit_frame(r60_context_t *ctx)
{
    const r60_frame_t *f = &ctx->frame;

    /* 保存为 last_frame */
    memcpy(&ctx->last_frame, f, sizeof(r60_frame_t));
    ctx->frame_count++;

    /* 按类型提取数据 */
    if (f->control == R60_CTRL_HEART && f->command == R60_CMD_RATE && f->length >= 1) {
        ctx->heartrate = f->data[0];
    } else if (f->control == R60_CTRL_BREATH && f->command == R60_CMD_RATE && f->length >= 1) {
        ctx->breathrate = f->data[0];
    } else if (f->control == R60_CTRL_CFG && f->command == R60_CMD_MOTION && f->length >= 1) {
        ctx->motion = f->data[0];
    } else if (f->control == R60_CTRL_HEART && f->command == R60_CMD_WAVEFORM && f->length > 0) {
        append_wave(ctx->heartwave, &ctx->heartwave_count,
                    R60_WAVE_BUF, f->data, f->length);
    } else if (f->control == R60_CTRL_BREATH && f->command == R60_CMD_WAVEFORM && f->length > 0) {
        append_wave(ctx->breathwave, &ctx->breathwave_count,
                    R60_WAVE_BUF, f->data, f->length);
    }
}

r60_status_t r60abd1_feed(r60_context_t *ctx, uint8_t byte)
{
    r60_frame_t *f = &ctx->frame;

    switch (ctx->state) {

    /* --- 同步阶段 --- */
    case ST_SYNC:
        if (byte == R60_HEAD1) {
            f->raw_len = 0;
            f->raw[f->raw_len++] = byte;
            ctx->data_pos = 0;
            ctx->state = ST_HEAD2;
        }
        return R60_AGAIN;

    /* --- 等待第二个帧头 --- */
    case ST_HEAD2:
        f->raw[f->raw_len++] = byte;
        if (byte == R60_HEAD2) {
            ctx->state = ST_CONTROL;
        } else {
            ctx->parse_errors++;
            ctx->state = ST_SYNC;
            return R60_ERR_HEAD;
        }
        return R60_AGAIN;

    /* --- Control & Command --- */
    case ST_CONTROL:
        f->raw[f->raw_len++] = byte;
        f->control = byte;
        ctx->state = ST_COMMAND;
        return R60_AGAIN;

    case ST_COMMAND:
        f->raw[f->raw_len++] = byte;
        f->command = byte;
        ctx->state = ST_LEN_HI;
        return R60_AGAIN;

    /* --- 长度字段 (2 bytes, big-endian) --- */
    case ST_LEN_HI:
        f->raw[f->raw_len++] = byte;
        f->length = (uint16_t)byte << 8;
        ctx->state = ST_LEN_LO;
        return R60_AGAIN;

    case ST_LEN_LO:
        f->raw[f->raw_len++] = byte;
        f->length |= byte;
        if (f->length > sizeof(f->data)) {
            ctx->parse_errors++;
            ctx->state = ST_SYNC;
            return R60_ERR_LENGTH;
        }
        ctx->state = (f->length == 0) ? ST_CHECK : ST_DATA;
        return R60_AGAIN;

    /* --- 载荷数据 --- */
    case ST_DATA:
        f->raw[f->raw_len++] = byte;
        f->data[ctx->data_pos++] = byte;
        if (ctx->data_pos >= f->length) {
            ctx->state = ST_CHECK;
        }
        return R60_AGAIN;

    /* --- 校验和 --- */
    case ST_CHECK:
        f->raw[f->raw_len++] = byte;
        {
            uint8_t expected = calc_sum(f->raw, 6 + f->length);
            if (expected != byte) {
                ctx->crc_errors++;
                ctx->state = ST_SYNC;
                return R60_ERR_CHECKSUM;
            }
        }
        ctx->state = ST_TAIL1;
        /* data_pos 复用为 tail_phase */
        ctx->data_pos = TAIL_WAIT_54;
        return R60_AGAIN;

    /* --- 帧尾 (2 bytes: 0x54 0x43) --- */
    case ST_TAIL1:
        f->raw[f->raw_len++] = byte;
        if (ctx->data_pos == TAIL_WAIT_54) {
            if (byte == R60_TAIL1) {
                ctx->data_pos = TAIL_WAIT_43;
                return R60_AGAIN;
            } else {
                ctx->parse_errors++;
                ctx->state = ST_SYNC;
                return R60_ERR_TAIL;
            }
        } else { /* TAIL_WAIT_43 */
            if (byte == R60_TAIL2) {
                commit_frame(ctx);
                ctx->state = ST_SYNC;
                return R60_OK;
            } else {
                ctx->parse_errors++;
                ctx->state = ST_SYNC;
                return R60_ERR_TAIL;
            }
        }
    }

    return R60_AGAIN;
}

/* ===== 便捷提取 ===== */
int r60abd1_get_heartrate(const r60_context_t *ctx)
{
    return ctx->heartrate;
}

int r60abd1_get_breathrate(const r60_context_t *ctx)
{
    return ctx->breathrate;
}

int r60abd1_get_motion(const r60_context_t *ctx)
{
    return ctx->motion;
}

const int16_t *r60abd1_get_heartwave(const r60_context_t *ctx, size_t *count)
{
    if (count) *count = ctx->heartwave_count;
    return ctx->heartwave;
}

const int16_t *r60abd1_get_breathwave(const r60_context_t *ctx, size_t *count)
{
    if (count) *count = ctx->breathwave_count;
    return ctx->breathwave;
}

uint64_t r60abd1_get_frame_count(const r60_context_t *ctx)
{
    return ctx->frame_count;
}

uint64_t r60abd1_get_parse_errors(const r60_context_t *ctx)
{
    return ctx->parse_errors;
}

uint64_t r60abd1_get_crc_errors(const r60_context_t *ctx)
{
    return ctx->crc_errors;
}

const r60_frame_t *r60abd1_get_last_frame(const r60_context_t *ctx)
{
    return &ctx->last_frame;
}

/* ===== 构建下行帧 ===== */
void r60abd1_build_frame(r60_tx_frame_t *out, uint8_t control,
                         uint8_t command, const uint8_t *data, uint16_t data_len)
{
    uint8_t *buf = out->raw;
    uint16_t idx = 0;

    buf[idx++] = R60_HEAD1;
    buf[idx++] = R60_HEAD2;
    buf[idx++] = control;
    buf[idx++] = command;
    buf[idx++] = (uint8_t)(data_len >> 8);
    buf[idx++] = (uint8_t)(data_len & 0xFF);

    for (uint16_t i = 0; i < data_len; i++) {
        buf[idx++] = data[i];
    }

    buf[idx] = calc_sum(buf, idx);
    buf[idx + 1] = R60_TAIL1;
    buf[idx + 2] = R60_TAIL2;
    idx += 3;

    out->len = idx;
}
