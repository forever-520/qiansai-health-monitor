/*
 * r60_uart.h — R60ABD1 雷达 UART 接收线程
 *
 * 负责：
 *   1. 打开并配置 UART 设备（/dev/ttyS4，115200 8-N-1）
 *   2. 创建独立线程读取 UART 字节并喂入 r60abd1_feed()
 *   3. 断线自动重连
 *
 * 集成方式：
 *   r60_uart_t *uart = r60_uart_start("/dev/ttyS4", 115200, radar_ctx);
 *   ...
 *   r60_uart_stop(uart);
 *
 * 要求：Linux（termios），不适用于 MinGW/Windows。
 */

#ifndef R60_UART_H
#define R60_UART_H

#include "r60abd1.h"

#ifdef __cplusplus
extern "C" {
#endif

/* UART 句柄（不透明指针） */
typedef struct r60_uart r60_uart_t;

/*
 * 启动 UART 接收线程。
 *
 * @param device   串口设备路径，如 "/dev/ttyS4"
 * @param baudrate 波特率，如 115200
 * @param ctx      已初始化的 r60abd1 驱动上下文
 * @return         UART 句柄，失败返回 NULL（stderr 有日志）
 */
r60_uart_t *r60_uart_start(const char *device, int baudrate, r60_context_t *ctx);

/*
 * 停止 UART 线程并关闭设备。可安全地在任意线程调用。
 * 调用后 uart 句柄失效。
 */
void r60_uart_stop(r60_uart_t *uart);

/*
 * 获取当前连接状态。
 * @return 1 = 已连接/正在读取，0 = 已断开/停止
 */
int r60_uart_is_connected(const r60_uart_t *uart);

/*
 * 向雷达发送下行配置帧。
 * @return 写入字节数，失败返回 -1
 */
int r60_uart_send(r60_uart_t *uart, const uint8_t *data, uint16_t len);

#ifdef __cplusplus
}
#endif

#endif /* R60_UART_H */
