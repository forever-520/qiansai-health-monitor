# health_monitor 开发工具

## R60ABD1 雷达模拟器（r60abd1_sim.py）

在无真实 R60ABD1 雷达硬件时，模拟其串口协议帧以测试驱动和数据链路。

### 安装依赖

```bash
pip install pyserial   # 仅串口模式需要
# 串口模式还需要 com0com（虚拟串口驱动）：https://com0com.sourceforge.net/
```

### 使用方式

```bash
# 方案 A：TCP 模式（推荐，免装驱动）
python r60abd1_sim.py --tcp 127.0.0.1:9000
# 然后用 socat 或 netcat 转发到实际串口，或修改驱动支持 TCP 输入

# 方案 B：串口模式（需先安装 com0com 创建虚拟串口对）
# 创建 CNCA0 ↔ CNCB0 对，将 CNCA0 重命名为 COM10
python r60abd1_sim.py --port COM10 --baud 115200

# 方案 C：标准输出
python r60abd1_sim.py --stdout > radar_data.bin
```

### 模拟场景

| 场景 | 心率 | 呼吸率 | 行为 |
|------|------|--------|------|
| `normal`（默认） | 65-80 bpm | 14-20 bpm | 正常呼吸，含呼吸性窦性心律不齐 |
| `sleep` | 50-65 bpm | 10-14 bpm | 低频低幅，微量运动 |
| `exercise` | 100-140 bpm | 24-35 bpm | 高频高幅，持续高强度运动 |
| `apnea` | 55-95 bpm | 0-6 bpm | 周期性呼吸暂停（20s 正常 + 8s 暂停） |
| `motion` | 65-130 bpm | 14-30 bpm | 40s 安静 + 20s 运动交替 |
| `noisy` | 60-85 bpm | 12-20 bpm | 偶发 CRC 错误帧、丢帧 |

### 波形模拟

- 心电波：简化 P-QRS-T 复合波形态，在心率数值帧之后发送
- 呼吸波：近正弦波，吸呼比约 1:2
- 波形数据为 byte [0,255]（中心 128），每帧 100 采样点

### 发送帧类型

| 类型 | Control | Command | 频率 |
|------|---------|---------|------|
| 心跳/配置 | 0x80 | 0x01 | 1 Hz |
| 运动强度 | 0x80 | 0x03 | 2 Hz |
| 心率数值 | 0x85 | 0x02 | 1 Hz |
| 呼吸率数值 | 0x81 | 0x02 | 1 Hz |
| 心电波形 | 0x85 | 0x05 | 1 Hz |
| 呼吸波形 | 0x81 | 0x05 | 1 Hz |
