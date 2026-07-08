# health_monitor — 床旁非接触生命体征监测系统

面向飞凌 RK3588 平台的嵌入式中生代竞赛项目，融合 FMCW 雷达（R60ABD1）和视觉摄像头（RK3576 从机），实现床旁实时心率和呼吸率监测、人体存在检测、异常告警。

```
┌─────────────┐    ┌──────────────┐    ┌───────────────┐    ┌──────────┐
│  R60ABD1    │    │  AI推理      │    │  Web后端桥接  │    │  浏览器  │
│  雷达驱动   │───→│  (SIM/RKNPU) │───→│  (CivetWeb)   │───→│  前端UI  │
│  src/radar/ │    │  src/ai/     │    │  src/webserver│    │  src/ui/ │
└─────────────┘    └──────────────┘    └───────────────┘    └──────────┘
```

---

## 目录结构

```
health_monitor/
├── cmake/
│   └── rk3588-toolchain.cmake    # RK3588 ARM64 交叉编译工具链
├── third_party/
│   └── civetweb/                 # 嵌入式 HTTP/WebSocket 服务器 (v1.17)
├── src/
│   ├── main.c                    # 主入口：集成所有模块
│   ├── radar/                    # R60ABD1 雷达驱动
│   │   ├── r60abd1.h             #   类型定义、API 声明
│   │   ├── r60abd1.c             #   四状态帧解析状态机、CRC、SPSC ring buffer
│   │   ├── test_main.c           #   单元测试 (PC 编译验证用)
│   │   └── CMakeLists.txt
│   ├── ai/                       # AI 推理模块
│   │   ├── ai_inference.h        #   API（创建/运行/销毁、结果结构体）
│   │   ├── ai_inference.c        #   SIM/RKNPU 双后端、YOLO 后处理
│   │   ├── ai_test.c             #   单元测试 (44 项)
│   │   └── CMakeLists.txt
│   ├── webserver/                # Web 后端桥接
│   │   ├── webserver.h
│   │   ├── webserver.c           #   CivetWeb 绑定、REST/WebSocket、广播线程
│   │   ├── vital_signs_state.h   #   共享状态类型定义
│   │   ├── vital_signs_state.c   #   状态管理（更新、告警、锁）
│   │   └── CMakeLists.txt
│   └── ui/                       # 浏览器前端静态文件
│       ├── index.html            #   主页面
│       ├── style.css             #   浅色医疗风格
│       └── app.js                #   逻辑：WebSocket 四类消息（vital_signs/
│                                 #   waveform/stats/alarms）、波形绘制、阈值划线
├── docs/                         # 项目文档（原 health_monitor/docs/）
└── CMakeLists.txt                # 顶层构建文件
```

---

## 模块说明

### radar/ — R60ABD1 雷达驱动

处理 UART 数据流，完成帧同步、帧类型解析和校验。支持四类帧：

| 帧类型 | 帧ID | 数据字段 | 推送频率 |
|--------|------|----------|----------|
| 配置帧 | 0x80/0x01 | 基础参数 | 仅初始化 |
| 运动帧 | 0x80/0x03 | 运动强度 | 人动时触发 |
| 心率帧 | 0x85/0x02 | 心率值 + 心电波形 | 约 1 Hz |
| 呼吸率帧 | 0x81/0x02 | 呼吸率 + 呼吸波形 | 约 1 Hz |
| 心电波帧 | 0x85/0x05 | 心电波形 | 随心率帧 |
| 呼吸波帧 | 0x81/0x05 | 呼吸波形 | 随呼吸率帧 |

内部维护两个 SPSC ring buffer 分别缓存心电波和呼吸波最近 512 点，供给上层读取。

**UART 接收层**（`r60_uart.h/c`，新增于 2026-05-25）：
- 独立线程读取 `/dev/ttyS4`（可通过 `RADAR_UART` 环境变量指定），逐字节喂入帧解析器
- 使用 termios 配置串口（8-N-1 无流控），select 超时轮询检测断线
- 断线后自动重连（3 秒间隔），支持下行帧发送（配置/调试指令）

### ai/ — AI 推理模块

双后端架构：

- **SIM 后端**（PC 开发用）：运行占位图像做 API 流程验证，无需实际模型
- **RKNPU 后端**（RK3588 部署用）：加载 RKNN 模型，调用 rknn_inputs_set / rknn_run / rknn_outputs_get，含 NMS 后处理

**注意**：YOLO 模型部署到 RK3588 NPU 时，训练必须将激活函数从 SiLU 改为 ReLU（否则 INT8 量化后精度劣化严重）。导出 ONNX 必须使用 Rockchip 官方 fork 的 ultralytics_yolov8，而非原生 ultralytics。

### webserver/ — Web 后端桥接

基于 CivetWeb 的单进程嵌入式服务器：

- **WebSocket** (`ws://host:8080/ws`): 30 Hz 推送 4 类 JSON 消息
- **REST API**: `GET /api/state`（实时状态）、`GET /api/device`（设备信息）、`GET/PUT /api/config`（配置读写）
- **静态文件**: `GET /` 提供前端 UI

WebSocket 消息格式：

```json
{"type":"vital_signs","data":{"hr":72.0,"br":16.0,"motion":3,"presence":"有人","stability":"稳定"}}
{"type":"waveform","heart":[128,130,...,125],"breath":[128,127,...,126]}
{"type":"stats","frame_count":1234,"parser_err":0,"crc_err":0,"online":true}
{"type":"alarms","alarms":[{"level":"warn","title":"心率偏高","time":"2026-05-25T12:00:00","detail":"超出阈值 100 bpm"}]}
```

波形数据为 byte 数组 [0, 255]（中心 128），前端 drawServerWave 直接绘制。

---

## 构建方法

### 1. PC 本地编译验证（MinGW / Linux）

```bash
cd health_monitor

# SIM 后端模式（无需模型）
cmake -B build -DAI_BACKEND=SIM
cmake --build build

# 运行（仅验证编译和模块集成）
./build/health_monitor ../src/ui

# 浏览器打开 http://localhost:8080
# 注意：SIM 模式下雷达数据和 AI 检测均为模拟值
```

### 2. RK3588 交叉编译

```bash
cd health_monitor

# 需要先安装 ARM64 GCC 交叉编译器
# apt install gcc-aarch64-linux-gnu (Ubuntu/Debian)
# 或将 cmake/rk3588-toolchain.cmake 的路径改为你的编译器路径

cmake -B build_rk3588 \
    -DCMAKE_TOOLCHAIN_FILE=cmake/rk3588-toolchain.cmake \
    -DAI_BACKEND=RKNPU

cmake --build build_rk3588

# 将 health_monitor 可执行文件复制到 RK3588 板端
```

### 3. RK3588 板端编译（如有 native GCC）

```bash
# 登入 RK3588 板端，在项目目录下
cmake -B build -DAI_BACKEND=RKNPU
cmake --build build
```

---

## RK3588 上电调试

详见 `rk3588-hardware-bringup-checklist.md`（位于 activity 笔记目录），包含完整的逐模块验证流程：

1. 硬件接线确认（UART / USB / 电源）
2. 雷达驱动单元测试（发送 0xAB 模式检测帧）
3. AI 推理循环（NPU 推理 + 可视化 debug）
4. Web 服务器集成验证（WebSocket + REST）
5. 全链路集成测试

---

## 当前开发状态

| 模块 | 状态 | 备注 |
|------|------|------|
| 雷达 R60ABD1 驱动 | ✅ 完成 | 帧同步、CRC、波形 ring buffer、单元测试通过 |
| AI 推理模块 | ✅ 完成 | SIM/RKNPU 双后端、后处理、44 项单元测试通过 |
| Web 后端桥接 | ✅ 完成 | CivetWeb 集成、WS 广播、REST API、波形格式已修复 |
| 前端 UI | ✅ 完成 | 医疗风格 UI、WebSocket 四类消息、波形绘制 |
| main.c 集成 | ✅ 完成 | 100 Hz 主循环、告警逻辑 |
| UART 接收层 | ✅ 完成 | termios 串口配置 + 独立线程 + 断线自动重连 |
| CMake 构建系统 | ✅ 完成 | 支持本地/RK3588 交叉编译 |
| **视觉模型训练** | 🔴 阻塞 | SLP 数据集压缩包已下载（17.5 GB），但为 ZipCrypto 加密，需从 ACLab 申请密码 |

---

## 待办项

- [ ] 获取 SLP 数据集密码并完成解压 → 提取训练数据 → YOLOv8 训练
- [ ] RK3588 板端部署：RKNN 模型转换、板端运行验证
- [ ] 多模态融合（雷达+视觉融合决策树可参考 activity 笔记）
- [ ] 睡眠状态机和异常检测
- [ ] 跌倒检测集成（YOLOv8-Pose + 几何规则，参考 activity 笔记）
