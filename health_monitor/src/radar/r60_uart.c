/*
 * r60_uart.c — R60ABD1 雷达 UART 接收线程实现
 *
 * 使用 termios 配置串口，创建独立线程轮询读取。
 * 断线后每 3 秒尝试重连，直到用户调用 r60_uart_stop()。
 *
 * 线程模型：
 *   主循环（main.c 中的 100Hz 循环） ← 轮询 r60abd1_get_*()
 *       ↑
 *   UART 线程（本文件） → 持续 r60abd1_feed(byte)
 *       ↑
 *   /dev/ttyS4 ← R60ABD1 雷达硬件
 *
 * 编译要求（RK3588 Linux）：
 *   #include <termios.h> 等 POSIX 头文件
 */

#include "r60_uart.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <pthread.h>

#include <termios.h>
#include <sys/select.h>

/* ===== 内部结构 ===== */
struct r60_uart {
    char device[64];            /* 设备路径 */
    int baudrate;               /* 波特率 */
    int fd;                     /* 设备文件描述符 (-1 = 未打开) */
    volatile int running;       /* 线程运行标志 */
    pthread_t thread;           /* 接收线程 */
    r60_context_t *radar_ctx;   /* 外部雷达驱动上下文 */
    pthread_mutex_t lock;       /* 保护 fd 和 running */
};

/* ===== 波特率转换表 (termios) ===== */
static speed_t baud_to_termios(int baud)
{
    switch (baud) {
        case 9600:     return B9600;
        case 19200:    return B19200;
        case 38400:    return B38400;
        case 57600:    return B57600;
        case 115200:   return B115200;
        case 230400:   return B230400;
        case 460800:   return B460800;
        case 921600:   return B921600;
        default:       return B115200;  /* 默认 115200 */
    }
}

/* ===== 串口打开与配置 ===== */
static int uart_open(const char *device, int baudrate)
{
    int fd = open(device, O_RDWR | O_NOCTTY | O_NDELAY);
    if (fd < 0) {
        fprintf(stderr, "[r60_uart] 无法打开 %s: %s\n", device, strerror(errno));
        return -1;
    }

    struct termios tio;
    memset(&tio, 0, sizeof(tio));

    /* 控制标志：8N1，无硬件流控 */
    tio.c_cflag = CLOCAL | CREAD | CS8;
    tio.c_cflag &= ~CRTSCTS;   /* 无硬件流控 */
    tio.c_cflag &= ~CSTOPB;    /* 1 个停止位 */
    tio.c_cflag &= ~PARENB;    /* 无校验 */

    /* 本地标志：不启用规范模式，原始输入 */
    tio.c_lflag = 0;

    /* 输入标志：原始模式 */
    tio.c_iflag = 0;
    tio.c_iflag &= ~(IXON | IXOFF | IXANY);  /* 无软件流控 */

    /* 输出标志：原始输出 */
    tio.c_oflag = 0;

    /* VMIN=1 表示至少读取 1 字节才返回，VTIME=0 表示无限等待 */
    tio.c_cc[VMIN]  = 1;
    tio.c_cc[VTIME] = 0;

    /* 设置波特率 */
    speed_t speed = baud_to_termios(baudrate);
    cfsetispeed(&tio, speed);
    cfsetospeed(&tio, speed);

    /* 清空缓冲区并应用配置 */
    tcflush(fd, TCIOFLUSH);
    if (tcsetattr(fd, TCSANOW, &tio) < 0) {
        fprintf(stderr, "[r60_uart] tcsetattr 失败: %s\n", strerror(errno));
        close(fd);
        return -1;
    }

    /* 清除 O_NDELAY，后续 read 可能阻塞 */
    int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags & ~O_NDELAY);

    printf("[r60_uart] 已打开 %s (波特率 %d)\n", device, baudrate);
    return fd;
}

/* ===== 接收线程 ===== */
static void *uart_read_thread(void *arg)
{
    r60_uart_t *uart = (r60_uart_t *)arg;
    uint8_t byte;

    while (uart->running) {
        /* 检查设备是否打开 */
        pthread_mutex_lock(&uart->lock);
        if (uart->fd < 0) {
            /* 尝试重连 */
            pthread_mutex_unlock(&uart->lock);
            fprintf(stderr, "[r60_uart] 设备未打开，3 秒后重试...\n");
            sleep(3);
            continue;
        }
        int fd = uart->fd;
        pthread_mutex_unlock(&uart->lock);

        /* 使用 select 检测可读（带超时，以便检查 running 标志） */
        fd_set read_fds;
        FD_ZERO(&read_fds);
        FD_SET(fd, &read_fds);

        struct timeval tv;
        tv.tv_sec  = 1;
        tv.tv_usec = 0;

        int ret = select(fd + 1, &read_fds, NULL, NULL, &tv);
        if (ret < 0) {
            if (errno == EINTR) continue;
            fprintf(stderr, "[r60_uart] select 错误: %s\n", strerror(errno));
            break;
        }
        if (ret == 0) continue;  /* 超时，检查 running 后继续 */

        if (!FD_ISSET(fd, &read_fds)) continue;

        /* 读取一个字节 */
        ssize_t n = read(fd, &byte, 1);
        if (n < 0) {
            if (errno == EINTR) continue;
            fprintf(stderr, "[r60_uart] read 错误: %s\n", strerror(errno));
            /* 标记断开，重连循环会处理 */
            pthread_mutex_lock(&uart->lock);
            close(uart->fd);
            uart->fd = -1;
            pthread_mutex_unlock(&uart->lock);
            continue;
        }
        if (n == 0) {
            /* EOF — 设备断开 */
            fprintf(stderr, "[r60_uart] 设备断开 (EOF)\n");
            pthread_mutex_lock(&uart->lock);
            close(uart->fd);
            uart->fd = -1;
            pthread_mutex_unlock(&uart->lock);
            continue;
        }

        /* 喂字节给雷达驱动状态机 */
        r60abd1_feed(uart->radar_ctx, byte);
    }

    printf("[r60_uart] 接收线程退出\n");
    return NULL;
}

/* ===== 公开 API ===== */

r60_uart_t *r60_uart_start(const char *device, int baudrate, r60_context_t *ctx)
{
    if (!device || !ctx) {
        fprintf(stderr, "[r60_uart] 参数错误\n");
        return NULL;
    }

    r60_uart_t *uart = (r60_uart_t *)calloc(1, sizeof(r60_uart_t));
    if (!uart) {
        fprintf(stderr, "[r60_uart] 分配失败\n");
        return NULL;
    }

    strncpy(uart->device, device, sizeof(uart->device) - 1);
    uart->baudrate = baudrate;
    uart->radar_ctx = ctx;
    uart->running = 1;
    pthread_mutex_init(&uart->lock, NULL);

    /* 首次打开设备 */
    uart->fd = uart_open(device, baudrate);

    /* 启动接收线程 */
    if (pthread_create(&uart->thread, NULL, uart_read_thread, uart) != 0) {
        fprintf(stderr, "[r60_uart] 线程创建失败\n");
        if (uart->fd >= 0) close(uart->fd);
        pthread_mutex_destroy(&uart->lock);
        free(uart);
        return NULL;
    }

    return uart;
}

void r60_uart_stop(r60_uart_t *uart)
{
    if (!uart) return;

    /* 通知线程退出 */
    uart->running = 0;

    /* 等待线程结束 */
    pthread_join(uart->thread, NULL);

    /* 关闭设备 */
    pthread_mutex_lock(&uart->lock);
    if (uart->fd >= 0) {
        close(uart->fd);
        uart->fd = -1;
    }
    pthread_mutex_unlock(&uart->lock);

    pthread_mutex_destroy(&uart->lock);
    printf("[r60_uart] 已停止\n");
    free(uart);
}

int r60_uart_is_connected(const r60_uart_t *uart)
{
    if (!uart) return 0;
    int connected;
    pthread_mutex_lock((pthread_mutex_t *)&uart->lock);
    connected = (uart->running && uart->fd >= 0);
    pthread_mutex_unlock((pthread_mutex_t *)&uart->lock);
    return connected;
}

int r60_uart_send(r60_uart_t *uart, const uint8_t *data, uint16_t len)
{
    if (!uart || !data || len == 0) return -1;

    pthread_mutex_lock(&uart->lock);
    if (uart->fd < 0) {
        pthread_mutex_unlock(&uart->lock);
        return -1;
    }
    ssize_t n = write(uart->fd, data, len);
    pthread_mutex_unlock(&uart->lock);

    return (int)n;
}
