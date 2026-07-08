#ifndef WEBSERVER_H
#define WEBSERVER_H

#ifdef __cplusplus
extern "C" {
#endif

/**
 * health_monitor Web 后端桥接
 *
 * 基于 CivetWeb 提供：
 *   - 静态文件服务（前端 UI 页面）
 *   - WebSocket 实时数据推送（30 Hz 推送 VitalSignsState）
 *   - REST API（状态查询 / 配置修改 / 历史数据）
 *
 * 用法：
 *   webserver_start("../src/ui", 8080);
 *   // ... 主循环 ...
 *   webserver_stop();
 */

/** 启动 Web 服务器
 *  @param ui_root 前端静态文件根目录路径（如 "../src/ui"）
 *  @param port    HTTP 监听端口（如 8080）
 *  @return 0=成功, -1=失败
 */
int webserver_start(const char *ui_root, int port);

/** 停止 Web 服务器 */
void webserver_stop(void);

/** 检查服务器是否在运行 */
int webserver_is_running(void);

#ifdef __cplusplus
}
#endif

#endif /* WEBSERVER_H */
