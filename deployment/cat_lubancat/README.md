# 鲁班猫（LubanCat）端 - 完整代码清单

**用户名：** `cat`  
**主机 IP（热点）：** `10.216.239.84`  
**主机 IP（局域网）：** `192.168.31.73`

---

## 完整文件清单

### 核心服务程序
| 文件 | 大小 | 功能说明 |
|------|------|----------|
| `radar_serial_bridge.py` | 13K | 雷达串口桥接 + 图像抓拍 HTTP 服务（合并单服务） |
| `start_radar_bridge.sh` | 160B | 启动脚本 |

### PC 端调试工具（可选）
| 文件 | 大小 | 功能说明 |
|------|------|----------|
| `pc_radar_viewer.py` | 1.8K | PC 端雷达数据查看器（文本模式） |
| `pc_radar_wave_viewer.py` | 4.0K | PC 端波形查看器（matplotlib） |

---

## radar_serial_bridge.py 核心功能

### 1. 雷达串口读取
**功能：**
- 连接 `/dev/ttyS10` @ 115200 baud
- 解析 R60ABD1 完整协议
- 实时更新内存数据结构

**协议解析：**
```python
# 帧格式：53 59 <长度> <命令字> <数据...> <校验和> 54 43
COMMANDS = {
    0x01: "人体存在信息",
    0x02: "体征参数",
    0x03: "体动参数",
    0x06: "方位信息",
    0x0B: "睡眠报告",
    0x85: "心率波形",
    0x86: "呼吸波形",
}
```

**数据结构：**
```python
{
    "human": {
        "exist": 0/1,
        "motion_state": 0/1/2,
        "motion_val": 0~100,
        "distance": cm,
        "x": cm, "y": cm, "z": cm
    },
    "heart": {
        "rate": bpm,
        "wave": [300点历史波形]
    },
    "breath": {
        "rate": 次/min,
        "state": 1/2/3/4,
        "wave": [300点历史波形]
    },
    "sleep": {
        "bed": 0/1/2,
        "state": 0/1/2/3,
        "score": 0~100,
        // ... 更多睡眠字段
    },
    "system": {
        "frame_count": 0,
        "parse_error_count": 0,
        "checksum_error_count": 0,
        "last_frame_hex": "..."
    }
}
```

### 2. 图像抓拍
**功能：**
- v4l2 抓取 `/dev/video11` 一帧（NV12 格式）
- ffmpeg 转 JPEG
- 返回二进制流

**实现：**
```python
def capture_frame():
    # v4l2-ctl 抓帧
    subprocess.run([
        "v4l2-ctl",
        "--device", "/dev/video11",
        "--set-fmt-video", "width=1920,height=1080,pixelformat=NV12",
        "--stream-mmap", "--stream-count=1",
        "--stream-to", "/tmp/frame.raw"
    ])
    
    # ffmpeg 转 JPEG
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-pix_fmt", "nv12",
        "-s", "1920x1080",
        "-i", "/tmp/frame.raw",
        "-frames:v", "1",
        "-q:v", "2",
        "/tmp/frame.jpg"
    ])
    
    return Path("/tmp/frame.jpg").read_bytes()
```

### 3. HTTP 服务
**监听：** `0.0.0.0:8000`

**接口：**
| 端点 | 方法 | 功能 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/radar/raw` | GET | 雷达真值 JSON（RK 用这个） |
| `/radar/display` | GET | 显示友好格式 JSON |
| `/camera/capture` | POST | 抓拍并返回 JPEG |

**示例响应：**
```bash
# 雷达数据
curl http://10.216.239.84:8000/radar/raw
{
  "human": {"exist": 1, "motion_state": 1, "motion_val": 12, ...},
  "heart": {"rate": 78, "wave": [...]},
  "breath": {"rate": 18, "state": 1, "wave": [...]},
  "sleep": {...},
  "system": {"frame_count": 1234, ...}
}

# 图像抓拍
curl -X POST http://10.216.239.84:8000/camera/capture -o frame.jpg
```

---

## 依赖软件

### Python 包
```bash
pip3 install pyserial
```

### 系统工具
```bash
# v4l2 工具
sudo apt install v4l-utils

# ffmpeg
sudo apt install ffmpeg

# 检查安装
v4l2-ctl --version
ffmpeg -version
```

### 串口权限
```bash
# 添加用户到 dialout 组
sudo usermod -aG dialout cat

# 重新登录后生效
groups | grep dialout
```

---

## 部署步骤

### 方案 1：系统级服务（推荐，上电自启）

```bash
# 1. 上传代码
scp radar_serial_bridge.py start_radar_bridge.sh cat@10.216.239.84:/home/cat/
ssh cat@10.216.239.84
chmod +x /home/cat/start_radar_bridge.sh

# 2. 创建系统服务
sudo tee /etc/systemd/system/radar_bridge.service > /dev/null <<'EOF'
[Unit]
Description=R60ABD1 radar HTTP bridge
After=network.target

[Service]
Type=simple
User=cat
WorkingDirectory=/home/cat
ExecStart=/home/cat/start_radar_bridge.sh
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# 3. 启用并启动
sudo systemctl daemon-reload
sudo systemctl enable radar_bridge.service
sudo systemctl start radar_bridge.service

# 4. 验证
sudo systemctl status radar_bridge.service
ss -ltn | grep :8000
curl http://localhost:8000/health
```

### 方案 2：用户级服务（需登录后启动）

```bash
# 1. 创建用户服务目录
mkdir -p ~/.config/systemd/user

# 2. 创建服务文件
cat > ~/.config/systemd/user/radar_bridge.service <<'EOF'
[Unit]
Description=R60ABD1 radar HTTP bridge
After=default.target

[Service]
Type=simple
ExecStart=/home/cat/start_radar_bridge.sh
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

# 3. 启用并启动
systemctl --user daemon-reload
systemctl --user enable radar_bridge.service
systemctl --user start radar_bridge.service

# 4. 验证
systemctl --user status radar_bridge.service
```

### 方案 3：手动启动（临时）

```bash
# 前台运行（调试用）
python3 /home/cat/radar_serial_bridge.py --serial-port /dev/ttyS10 --baud 115200 --host 0.0.0.0 --port 8000

# 后台运行
nohup bash /home/cat/start_radar_bridge.sh > ~/radar.log 2>&1 &
```

---

## 命令行参数

```bash
python3 radar_serial_bridge.py --help

# 可选参数
--serial-port /dev/ttyS10  # 串口设备（默认 /dev/ttyS10）
--baud 115200              # 波特率（默认 115200）
--host 0.0.0.0             # HTTP 监听地址（默认 0.0.0.0）
--port 8000                # HTTP 监听端口（默认 8000）
```

---

## 故障排查

### 服务未启动
```bash
# 系统级服务
sudo systemctl status radar_bridge.service
sudo journalctl -u radar_bridge.service -n 50

# 用户级服务
systemctl --user status radar_bridge.service
journalctl --user -u radar_bridge.service -n 50

# 检查端口
ss -ltn | grep :8000

# 检查进程
ps -ef | grep radar_serial_bridge.py
```

### 串口读取失败
```bash
# 检查串口设备
ls -l /dev/ttyS*

# 检查权限
groups | grep dialout

# 测试串口
python3 -c "import serial; s=serial.Serial('/dev/ttyS10', 115200); print('OK')"

# 查看串口数据（十六进制）
cat /dev/ttyS10 | hexdump -C | head -20
```

### 摄像头抓拍失败
```bash
# 检查摄像头设备
ls -l /dev/video*

# 查看摄像头信息
v4l2-ctl --device /dev/video11 --all

# 手动抓帧测试
v4l2-ctl --device /dev/video11 \
  --set-fmt-video width=1920,height=1080,pixelformat=NV12 \
  --stream-mmap --stream-count=1 --stream-to /tmp/test.raw

# 转 JPEG 测试
ffmpeg -f rawvideo -pix_fmt nv12 -s 1920x1080 \
  -i /tmp/test.raw -frames:v 1 -q:v 2 /tmp/test.jpg
```

### HTTP 无响应
```bash
# 测试健康检查
curl http://localhost:8000/health

# 测试雷达数据
curl http://localhost:8000/radar/raw | jq .

# 测试图像抓拍
curl -X POST http://localhost:8000/camera/capture -o test.jpg

# 查看 HTTP 日志
sudo journalctl -u radar_bridge.service -f
```

---

## PC 端调试工具使用

### pc_radar_viewer.py（文本查看器）
```bash
# 实时显示雷达数据
python3 pc_radar_viewer.py http://10.216.239.84:8000/radar/raw

# 输出示例
存在: 有人  体动: 静止(12)  距离: 35cm
心率: 78 bpm  呼吸: 18 次/min  在床: 入床
睡眠: 清醒  评分: 0
帧数: 1234
```

### pc_radar_wave_viewer.py（波形查看器）
```bash
# matplotlib 实时波形
python3 pc_radar_wave_viewer.py http://10.216.239.84:8000/radar/raw

# 需要安装
pip3 install matplotlib requests
```

---

## 性能指标

- **串口读取延迟：** <10ms
- **HTTP 响应时间：** <50ms（雷达数据）
- **图像抓拍时间：** 1~2 秒（v4l2 + ffmpeg）
- **内存占用：** ~15MB
- **CPU 占用：** <2%（空闲时）

---

## 版本说明

**当前版本：** 2026-06-18 合并单服务版

**核心特性：**
- 雷达数据和图像抓拍合并到单一 `8000` 端口
- 不再依赖独立的 `9001` 图传服务
- 支持系统级服务上电自启
- 串口断线自动重连

**已知限制：**
- 摄像头设备路径硬编码 `/dev/video11`
- 图像格式固定 1920x1080 NV12
- 用户级服务需登录后才启动
- 波形历史固定 300 点
