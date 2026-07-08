/*
 * health_monitor — 床旁非接触生命体征监测系统
 *
 * 集成模块:
 *   radar/      R60ABD1 雷达驱动（帧解析、CRC、SPSC ring buffer）
 *   ai/         AI 推理（YOLO 检测、SIM/RKNPU 双后端、后处理）
 *   webserver/  CivetWeb 桥接（静态文件、WebSocket 30Hz 推送、REST API）
 *   ui/         浏览器前端（静态 HTML/CSS/JS）
 *
 * 编译: cmake -B build && cmake --build build
 * 运行: ./build/health_monitor ../src/ui
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>

#include "radar/r60abd1.h"
#include "radar/r60_uart.h"
#include "ai/ai_inference.h"
#include "webserver/webserver.h"
#include "webserver/vital_signs_state.h"

/* 全局标志：SIGINT 时优雅退出 */
static volatile int g_quit = 0;
static void sigint_handler(int sig)
{
    (void)sig;
    g_quit = 1;
}

/* 告警阈值 */
#define HR_MAX    100.0
#define HR_MIN     50.0
#define BR_MAX     24.0
#define BR_MIN      8.0

/* 告警级别常量 */
#define ALARM_WARN   "warn"
#define ALARM_NORMAL "normal"
#define ALARM_INFO   "info"

int main(int argc, char *argv[])
{
    const char *ui_root = (argc > 1) ? argv[1] : "../src/ui";
    int port = (argc > 2) ? atoi(argv[2]) : 8080;

    printf("health_monitor v0.1 — 床旁非接触生命体征监测\n");
    printf("  UI 根目录:  %s\n", ui_root);
    printf("  HTTP 端口:  %d\n\n", port);

    /* 初始化全局共享状态 */
    vital_signs_state_init();

    /* ----- 初始化雷达 ----- */
    r60_context_t *radar_ctx = r60abd1_init();
    r60_uart_t *radar_uart = NULL;
    if (!radar_ctx) {
        fprintf(stderr, "[main] 雷达驱动初始化失败，以无雷达模式运行\n");
    } else {
        printf("[main] 雷达驱动已就绪，尝试打开 UART...\n");
        const char *uart_dev = getenv("RADAR_UART");
        if (!uart_dev) uart_dev = "/dev/ttyS4";
        int baud = 115200;
        const char *baud_env = getenv("RADAR_BAUD");
        if (baud_env) baud = atoi(baud_env);
        radar_uart = r60_uart_start(uart_dev, baud, radar_ctx);
        if (!radar_uart) {
            fprintf(stderr, "[main] UART 打开失败 %s，以无雷达模式运行\n", uart_dev);
        } else {
            printf("[main] UART 线程已启动: %s @ %d baud\n", uart_dev, baud);
        }
    }

    /* ----- 初始化 AI ----- */
    ai_context_t *ai_ctx = ai_create(NULL, AI_MODEL_BED_DETECT, AI_BACKEND_SIM, -1);
    if (!ai_ctx) {
        fprintf(stderr, "[main] AI 初始化失败，以无AI模式运行\n");
    }

    /* ----- 启动 Web 服务器 ----- */
    if (webserver_start(ui_root, port) != 0) {
        fprintf(stderr, "[main] Web 服务器启动失败\n");
        return 1;
    }

    /* ----- 信号处理 ----- */
    signal(SIGINT, sigint_handler);

    /* ----- 主循环 (100 Hz) ----- */
    printf("[main] 进入主循环\n");
    uint64_t loop_count = 0;

    while (!g_quit) {
        /*
         * 雷达数据处理:
         * 实际 RK3588 上由 UART RX 线程调用 r60abd1_feed() 逐字节喂入,
         * 主循环只需检查并提取已完成帧的数据。此处检测 R60_OK 即更新共享状态。
         * PC 模拟模式下无 UART 数据，雷达数据保持为初始零值。
         */
        if (radar_ctx) {
            /* 从波形缓冲区提取最新数据 */
            size_t hw_count = 0, bw_count = 0;
            const int16_t *hw = r60abd1_get_heartwave(radar_ctx, &hw_count);
            const int16_t *bw = r60abd1_get_breathwave(radar_ctx, &bw_count);

            /* 转换为 float 波形缓冲区（int16→float 映射 -2048~2047 → -1.0~1.0）*/
            float heart_float[512], breath_float[512];
            int hw_n = (int)(hw_count > 512 ? 512 : hw_count);
            int bw_n = (int)(bw_count > 512 ? 512 : bw_count);
            for (int i = 0; i < hw_n; i++) heart_float[i] = hw[i] / 2048.0f;
            for (int i = 0; i < bw_n; i++) breath_float[i] = bw[i] / 2048.0f;

            vital_signs_update_radar(
                (double)r60abd1_get_heartrate(radar_ctx),
                (double)r60abd1_get_breathrate(radar_ctx),
                (uint8_t)r60abd1_get_motion(radar_ctx),
                (uint8_t)(r60abd1_get_motion(radar_ctx) > 0 ? PRESENCE_STILL : PRESENCE_NONE),
                heart_float, hw_n,
                breath_float, bw_n);

            /* 同步雷达驱动内部统计 */
            vital_signs_lock();
            g_vital_state.frame_count = (uint32_t)r60abd1_get_frame_count(radar_ctx);
            g_vital_state.parser_errors = (uint32_t)r60abd1_get_parse_errors(radar_ctx);
            g_vital_state.crc_errors = (uint32_t)r60abd1_get_crc_errors(radar_ctx);
            vital_signs_unlock();
        }

        /* AI 推理 (SIM 模式下使用 1x1 占位图像) */
        if (ai_ctx) {
            static uint8_t fake_pixel[3] = {0};
            ai_result_t ai_result;
            if (ai_result_init(&ai_result, 10) == 0) {
                if (ai_run(ai_ctx, fake_pixel, 1, 1, 3, &ai_result) == 0) {
                    vital_signs_update_vision(
                        (uint8_t)ai_result.person_in_bed,
                        ai_result.top_confidence,
                        0,      /* fall_detected: 待姿态检测集成 */
                        0.0f);  /* fall_conf: 待姿态检测集成 */
                }
                ai_result_free(&ai_result);
            }
        }

        /* 告警逻辑 */
        if (g_vital_state.heart_rate > HR_MAX) {
            vital_signs_add_alarm("心率偏高", "超出阈值 100 bpm", ALARM_WARN);
        }
        if (g_vital_state.heart_rate > 0.0 && g_vital_state.heart_rate < HR_MIN) {
            vital_signs_add_alarm("心率偏低", "低于阈值 50 bpm", ALARM_WARN);
        }
        if (g_vital_state.breath_rate > BR_MAX) {
            vital_signs_add_alarm("呼吸率偏高", "超出阈值 24 rpm", ALARM_WARN);
        }
        if (g_vital_state.breath_rate > 0.0 && g_vital_state.breath_rate < BR_MIN) {
            vital_signs_add_alarm("呼吸率偏低", "低于阈值 8 rpm", ALARM_WARN);
        }

        /* 每秒更新一次时间戳 */
        if (loop_count % 100 == 0) {
            vital_signs_tick();
        }

        loop_count++;
        usleep(10000); /* 10 ms ≈ 100 Hz */
    }

    /* ----- 清理 ----- */
    printf("\n[main] 正在关闭...\n");
    webserver_stop();
    if (ai_ctx) ai_destroy(ai_ctx);
    if (radar_uart) r60_uart_stop(radar_uart);
    if (radar_ctx) r60abd1_free(radar_ctx);
    printf("[main] 已退出\n");

    return 0;
}
