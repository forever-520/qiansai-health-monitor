# YOLOv8 床位状态检测

`yolo_bed_state` 目录包含床位占用状态检测的训练结果、权重、指标和样例数据。

## 类别

```text
0 = occupied_bed
1 = empty_bed
```

## 内容

| 路径 | 说明 |
|---|---|
| `model/best.pt` | 最佳训练权重 |
| `model/last.pt` | 最后一轮训练权重 |
| `config/data.yaml` | 数据集配置 |
| `config/args.yaml` | 训练参数 |
| `metrics/` | 训练曲线、混淆矩阵、PR/F1/P/R 曲线 |
| `predict_results/` | 验证批次预测图与标签对照图 |
| `sample_train/` | 训练集抽样 |
| `sample_val/` | 验证集抽样 |
| `test_images_unlabeled/` | 无标签演示图片 |

## 推理示例

安装 Ultralytics：

```bash
pip install ultralytics
```

运行预测：

```bash
yolo detect predict model=yolo_bed_state/model/best.pt source=yolo_bed_state/test_images_unlabeled
```

## 指标

训练结果摘要：

```text
precision: 0.9358
recall: 0.9003
mAP50: 0.9370
mAP50-95: 0.6201
```

原始项目没有独立带标签测试集，`config/data.yaml` 中 `test` 指向验证集。仓库额外附带 6 张无标签图片用于演示预测。

