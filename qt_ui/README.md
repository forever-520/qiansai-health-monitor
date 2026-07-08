# qt

嵌入式比赛：床旁非接触生命体征监测 Python Qt 版。

这个版本使用 Python 3.11 + PySide6 实现，适合作为后续迁移到 RK3588 / Linux Qt 的界面原型。

## 上位机预览

板子或雷达模块还没接入时，双击 `run_mock.bat` 进入模拟数据模式：

```powershell
.\run_mock.bat
```

这个模式会强制使用 1024x600 紧凑布局，并生成模拟心率、呼吸、距离、睡眠和串口帧数据，方便先检查界面。

## 真实运行

双击 `run.bat`，或在当前目录执行：

```powershell
python -m pip install -r requirements.txt
python main.py
```

## 文件

- `main.py`：主程序，包含三页界面和动态波形绘制
- `requirements.txt`：Python 依赖
- `run.bat`：Windows 下快速启动脚本

## 技术说明

- UI 框架：PySide6，也就是 Python Qt
- 实时波形：Qt 自绘，使用 `QPainter` 模拟 ECG 心率和呼吸波形
- 页面结构：实时监测、历史摘要、设备调试
