#include "webserver.h"
#include "vital_signs_state.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
#include <unistd.h>

/* ================================================================== */
/*  CivetWeb （置于 third_party/civetweb/）                            */
/*  编译宏: -DNO_CGI -DNO_SSL -DUSE_WEBSOCKET                         */
/* ================================================================== */
#include "civetweb.h"

/* ================================================================== */
/*  内部状态                                                            */
/* ================================================================== */

static struct mg_context *g_ctx = NULL;
static volatile int g_running = 0;

/* ----- 已连接的 WebSocket 客户端列表（广播用） ----- */
typedef struct WSClient {
    struct mg_connection *conn;
    struct WSClient *next;
} WSClient;

static WSClient *g_ws_clients = NULL;
static pthread_mutex_t g_ws_mutex = PTHREAD_MUTEX_INITIALIZER;

static void ws_client_add(struct mg_connection *conn)
{
    pthread_mutex_lock(&g_ws_mutex);
    WSClient *c = (WSClient *)malloc(sizeof(WSClient));
    if (c) {
        c->conn = conn;
        c->next = g_ws_clients;
        g_ws_clients = c;
    }
    pthread_mutex_unlock(&g_ws_mutex);
}

static void ws_client_remove(const struct mg_connection *conn)
{
    pthread_mutex_lock(&g_ws_mutex);
    WSClient **pp = &g_ws_clients;
    while (*pp) {
        if ((*pp)->conn == conn) {
            WSClient *tmp = *pp;
            *pp = (*pp)->next;
            free(tmp);
            break;
        }
        pp = &(*pp)->next;
    }
    pthread_mutex_unlock(&g_ws_mutex);
}

/* 向所有 WS 客户端广播数据 */
static void ws_broadcast(const char *data, size_t len)
{
    pthread_mutex_lock(&g_ws_mutex);
    for (WSClient *c = g_ws_clients; c; c = c->next) {
        mg_websocket_write(c->conn, MG_WEBSOCKET_OPCODE_TEXT, data, len);
    }
    pthread_mutex_unlock(&g_ws_mutex);
}

/* ================================================================== */
/*  JSON 序列化 — 四类消息类型 (匹配 app.js 的 switch/case)            */
/* ================================================================== */

/* 工具：浮点数组 → byte 数组序列化（降采样至 64 点）
 *
 * float [-1.0, 1.0] → uint8 [0, 255] (中心 128)
 * 前端 drawServerWave 期望 byte 值：128 = 基线，0 = 底部，255 = 顶部
 */
static int append_wave_bytes(char *buf, size_t cap,
                              const float *arr, int len)
{
    int pos = 0;
    pos += snprintf(buf + pos, cap - (size_t)pos, "[");
    int step = (len > 64) ? (len / 64) : 1;
    int count = 0;
    for (int i = 0; i < len && count < 64; i += step) {
        count++;
        if (count > 1) pos += snprintf(buf + pos, cap - (size_t)pos, ",");
        /* float [-1.0, 1.0] → byte [0, 255], 中心 128 */
        int bval = (int)(arr[i] * 127.0f) + 128;
        if (bval < 0) bval = 0;
        if (bval > 255) bval = 255;
        pos += snprintf(buf + pos, cap - (size_t)pos, "%d", bval);
    }
    pos += snprintf(buf + pos, cap - (size_t)pos, "]");
    return pos;
}

/* --- 消息1: vital_signs --- */
static char *serialize_vitals_json(void)
{
    char *json = (char *)malloc(512);
    if (!json) return NULL;

    vital_signs_lock();

    /* presence: 枚举值 → "有人" / "无人" 字符串 */
    const char *presence_str = (g_vital_state.presence_state == PRESENCE_NONE)
                                ? "\u65E0\u4EBA" : "\u6709\u4EBA";
    /* stability: 体动强度阈值判断 */
    const char *stability_str = (g_vital_state.motion_intensity > 5)
                                 ? "\u4F53\u52A8\u4E2D" : "\u7A33\u5B9A";

    int pos = snprintf(json, 512,
        "{"
        "\"type\":\"vital_signs\","
        "\"data\":{"
        "\"hr\":%.1f,"
        "\"br\":%.1f,"
        "\"motion\":%u,"
        "\"presence\":\"%s\","
        "\"stability\":\"%s\""
        "}"
        "}",
        g_vital_state.heart_rate,
        g_vital_state.breath_rate,
        (unsigned)g_vital_state.motion_intensity,
        presence_str,
        stability_str);

    vital_signs_unlock();
    (void)pos;
    return json;
}

/* --- 消息2: waveform --- */
static char *serialize_waveform_json(void)
{
    char *json = (char *)malloc(3072);
    if (!json) return NULL;

    /* 先拼波形容器 */
    int pos = 0;
    pos += snprintf(json + pos, 3072 - (size_t)pos,
        "{\"type\":\"waveform\",\"heart\":");

    vital_signs_lock();
    pos += append_wave_bytes(json + pos, 3072 - (size_t)pos,
                               g_vital_state.heart_linear, VITAL_WAVE_POINTS);
    pos += snprintf(json + pos, 3072 - (size_t)pos, ",\"breath\":");
    pos += append_wave_bytes(json + pos, 3072 - (size_t)pos,
                               g_vital_state.breath_linear, VITAL_WAVE_POINTS);
    vital_signs_unlock();

    pos += snprintf(json + pos, 3072 - (size_t)pos, "}");
    (void)pos;
    return json;
}

/* --- 消息3: stats --- */
static char *serialize_stats_json(void)
{
    char *json = (char *)malloc(256);
    if (!json) return NULL;

    vital_signs_lock();
    int pos = snprintf(json, 256,
        "{"
        "\"type\":\"stats\","
        "\"frame_count\":%u,"
        "\"parser_err\":%u,"
        "\"crc_err\":%u,"
        "\"online\":%s"
        "}",
        (unsigned)g_vital_state.frame_count,
        (unsigned)g_vital_state.parser_errors,
        (unsigned)g_vital_state.crc_errors,
        "true" /* 雷达在线，稍后可从 radar_is_online 读取 */);
    vital_signs_unlock();
    (void)pos;
    return json;
}

/* --- 消息4: alarms --- */
static char *serialize_alarms_json(void)
{
    char *json = (char *)malloc(1024);
    if (!json) return NULL;

    vital_signs_lock();

    int pos = snprintf(json, 1024,
        "{\"type\":\"alarms\",\"alarms\":[");

    for (int i = 0; i < g_vital_state.alarm_count && i < VITAL_MAX_ALARMS; i++) {
        if (i > 0) pos += snprintf(json + pos, 1024 - (size_t)pos, ",");
        pos += snprintf(json + pos, 1024 - (size_t)pos,
            "{"
            "\"level\":\"%s\","
            "\"title\":\"%s\","
            "\"time\":\"%s\","
            "\"detail\":\"%s\""
            "}",
            g_vital_state.alarms[i].level,
            g_vital_state.alarms[i].title,
            g_vital_state.alarms[i].time,
            g_vital_state.alarms[i].detail);
    }

    pos += snprintf(json + pos, 1024 - (size_t)pos, "]}");

    vital_signs_unlock();
    (void)pos;
    return json;
}

/* ================================================================== */
/*  广播线程 （30 Hz） — 发送 4 种消息类型                             */
/* ================================================================== */

static void *broadcast_thread(void *arg)
{
    (void)arg;
    const long interval_ns = 33333333L; /* 30 Hz */
    uint64_t wave_frame = 0;

    while (g_running) {
        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        uint64_t deadline = (uint64_t)ts.tv_sec * 1000000000ULL
                          + (uint64_t)ts.tv_nsec + interval_ns;

        /* 30 Hz: 每次发送 vital_signs */
        {
            char *json = serialize_vitals_json();
            if (json) {
                ws_broadcast(json, strlen(json));
                free(json);
            }
        }

        /* 5 Hz: 状态统计 */
        if (wave_frame % 6 == 0) {
            char *json = serialize_stats_json();
            if (json) {
                ws_broadcast(json, strlen(json));
                free(json);
            }
        }

        /* ~3 Hz: 波形（大负载，降低频率） */
        if (wave_frame % 10 == 0) {
            char *json = serialize_waveform_json();
            if (json) {
                ws_broadcast(json, strlen(json));
                free(json);
            }
        }

        /* 1 Hz: 告警 */
        if (wave_frame % 30 == 0) {
            char *json = serialize_alarms_json();
            if (json) {
                ws_broadcast(json, strlen(json));
                free(json);
            }
        }

        wave_frame++;

        clock_gettime(CLOCK_MONOTONIC, &ts);
        uint64_t now = (uint64_t)ts.tv_sec * 1000000000ULL
                     + (uint64_t)ts.tv_nsec;
        if (deadline > now) {
            ts.tv_sec  = 0;
            ts.tv_nsec = (long)(deadline - now);
            nanosleep(&ts, NULL);
        }
    }
    return NULL;
}

/* ================================================================== */
/*  WebSocket 回调                                                     */
/* ================================================================== */

static int ws_connect_handler(const struct mg_connection *conn, void *cbdata)
{
    (void)cbdata;
    (void)conn;
    return 0; /* 0 = 接受连接 */
}

static void ws_ready_handler(struct mg_connection *conn, void *cbdata)
{
    (void)cbdata;
    ws_client_add(conn);
}

static int ws_data_handler(struct mg_connection *conn,
                           int bits, char *data, size_t data_len,
                           void *cbdata)
{
    (void)conn; (void)bits; (void)data; (void)data_len; (void)cbdata;
    return 1; /* 保持连接 */
}

static void ws_close_handler(const struct mg_connection *conn, void *cbdata)
{
    (void)cbdata;
    ws_client_remove(conn);
}

/* ================================================================== */
/*  REST API 回调                                                      */
/* ================================================================== */

static int api_handler(struct mg_connection *conn, void *cbdata)
{
    (void)cbdata;
    const struct mg_request_info *ri = mg_get_request_info(conn);
    char buf[4096];

    if (strcmp(ri->local_uri, "/api/state") == 0) {
        char *json = serialize_vitals_json();
        if (!json) {
            mg_printf(conn, "HTTP/1.1 500\r\nContent-Length: 0\r\n\r\n");
            return 1;
        }
        mg_printf(conn,
                  "HTTP/1.1 200 OK\r\n"
                  "Content-Type: application/json\r\n"
                  "Content-Length: %d\r\n"
                  "Access-Control-Allow-Origin: *\r\n"
                  "\r\n%s",
                  (int)strlen(json), json);
        free(json);
        return 1;
    }

    if (strcmp(ri->local_uri, "/api/device") == 0) {
        int n;
        vital_signs_lock();
        n = snprintf(buf, sizeof(buf),
            "{"
            "\"serial\":\"/dev/ttyS4\","
            "\"baud\":115200,"
            "\"uptime\":%d,"
            "\"frameCount\":%u,"
            "\"parserErr\":%u,"
            "\"crcErr\":%u"
            "}",
            (int)g_vital_state.uptime_sec,
            (unsigned)g_vital_state.frame_count,
            (unsigned)g_vital_state.parser_errors,
            (unsigned)g_vital_state.crc_errors);
        vital_signs_unlock();

        mg_printf(conn,
                  "HTTP/1.1 200 OK\r\n"
                  "Content-Type: application/json\r\n"
                  "Content-Length: %d\r\n"
                  "Access-Control-Allow-Origin: *\r\n"
                  "\r\n%s", n, buf);
        return 1;
    }

    if (strcmp(ri->local_uri, "/api/config") == 0) {
        int n = snprintf(buf, sizeof(buf),
            "{"
            "\"hrMax\":100,\"hrMin\":50,"
            "\"brMax\":24,\"brMin\":8,"
            "\"stillThreshold\":5.0,"
            "\"stillTimeout\":1800"
            "}");
        mg_printf(conn,
                  "HTTP/1.1 200 OK\r\n"
                  "Content-Type: application/json\r\n"
                  "Content-Length: %d\r\n"
                  "Access-Control-Allow-Origin: *\r\n"
                  "\r\n%s", n, buf);
        return 1;
    }

    /* PUT /api/config — 配置更新 */
    if (strcmp(ri->local_uri, "/api/config") == 0
        && strcmp(ri->request_method, "PUT") == 0) {
        /* 读取请求体（未来扩展） */
        mg_printf(conn,
                  "HTTP/1.1 200 OK\r\n"
                  "Content-Type: application/json\r\n"
                  "Content-Length: 4\r\n"
                  "\r\nnull");
        return 1;
    }

    return 0; /* civetweb 返回 404 */
}

/* ================================================================== */
/*  公开接口                                                            */
/* ================================================================== */

int webserver_start(const char *ui_root, int port)
{
    if (g_ctx) return -1;

    char port_str[16];
    snprintf(port_str, sizeof(port_str), "%d", port);

    const char *options[] = {
        "document_root",      ui_root,
        "listening_ports",    port_str,
        "num_threads",        "8",
        "request_timeout_ms", "10000",
        "enable_auth_domain_check", "no",
        NULL
    };

    struct mg_callbacks callbacks;
    memset(&callbacks, 0, sizeof(callbacks));

    g_ctx = mg_start(&callbacks, NULL, options);
    if (!g_ctx) {
        fprintf(stderr, "[webserver] 启动失败, port %d\n", port);
        return -1;
    }

    printf("[webserver] health_monitor UI:  http://localhost:%d\n", port);
    printf("[webserver] WebSocket:           ws://localhost:%d/ws\n", port);
    printf("[webserver] REST API:            http://localhost:%d/api/state\n", port);

    /* 注册 WebSocket 端点 */
    mg_set_websocket_handler(g_ctx, "/ws",
                             ws_connect_handler,
                             ws_ready_handler,
                             ws_data_handler,
                             ws_close_handler,
                             NULL);

    /* 注册 REST API 路由 */
    mg_set_request_handler(g_ctx, "/api/state",  api_handler, NULL);
    mg_set_request_handler(g_ctx, "/api/device",  api_handler, NULL);
    mg_set_request_handler(g_ctx, "/api/config",  api_handler, NULL);

    g_running = 1;

    /* 启动广播线程 */
    pthread_t tid;
    pthread_create(&tid, NULL, broadcast_thread, NULL);
    pthread_detach(tid);

    return 0;
}

void webserver_stop(void)
{
    g_running = 0;
    if (g_ctx) {
        mg_stop(g_ctx);
        g_ctx = NULL;
    }
    /* 清理 WS 客户端列表 */
    pthread_mutex_lock(&g_ws_mutex);
    WSClient *c = g_ws_clients;
    while (c) {
        WSClient *next = c->next;
        free(c);
        c = next;
    }
    g_ws_clients = NULL;
    pthread_mutex_unlock(&g_ws_mutex);
}

int webserver_is_running(void)
{
    return (g_ctx != NULL) ? 1 : 0;
}
