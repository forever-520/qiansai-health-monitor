# 部署说明

## LubanCat

```bash
cd deployment/cat_lubancat
chmod +x start_radar_bridge.sh
./start_radar_bridge.sh
```

默认串口为 `/dev/ttyS10`，摄像头为 `/dev/video11`。如硬件不同，可修改 `start_radar_bridge.sh`。

图像缓存参数：

```text
--camera-width 1280
--camera-height 720
--jpeg-quality 75
--camera-cache-interval 8
```

## RK Web

```bash
cd deployment/rk_web_ui
cp rk_stack.env.example rk_stack.env
```

编辑 `rk_stack.env`：

```text
WEB_USER=admin
WEB_PASS=your-password
LUBANCAT_HOST=auto
```

启动：

```bash
chmod +x start_rk_stack.sh
./start_rk_stack.sh
```

访问：

```text
http://<RK_IP>:8081
```

## 外网访问

`deployment/rk_web_ui` 中提供 cloudflared 和 frp 示例配置。外网访问前必须设置强密码。

## 开机自启

RK Web 端可参考：

```bash
cd deployment/rk_web_ui
chmod +x install_systemd.sh
./install_systemd.sh
```

部署完成后可使用：

```bash
systemctl --user status qiansai-web.service
```

