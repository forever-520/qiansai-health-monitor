bed_status_yolov8_bed_state_v3_send 发送说明

这个目录整理自床位占用状态检测 YOLOv8 训练结果。

最终训练使用的数据配置见：
config/data.yaml

最终训练结果整理在：
model/、metrics/、predict_results/

原始数据集情况：
1. train 训练集：有
   images/train: 646 张图片
   labels/train: 646 个标签文件

2. val 验证集：有
   images/val: 162 张图片
   labels/val: 162 个标签文件

3. 独立 test 测试集：没有单独文件夹
   没有 dataset/bed_state_v3/images/test
   没有 dataset/bed_state_v3/labels/test

说明：
data.yaml 里写了 test: images/val，所以如果按 YOLO 的 test 配置运行，实际会使用 val 验证集作为 test。
test_images_unlabeled/ 中有 6 张无标签图片，可以用于预测演示，但它不是带标签的正式测试集。

类别：
0 = occupied_bed
1 = empty_bed

训练指标最后一轮大致为：
precision: 0.9358
recall: 0.9003
mAP50: 0.9370
mAP50-95: 0.6201

包内内容：
1. model/
   best.pt 和 last.pt，来自最终训练目录。

2. config/
   data.yaml 和 args.yaml。

3. metrics/
   results.png、混淆矩阵、PR/F1/P/R 曲线、results.csv 等训练指标文件。

4. predict_results/
   val_batch*_pred.jpg 是模型预测效果图。
   val_batch*_labels.jpg 是人工标注对照图。

5. sample_train/
   从原始训练集抽取的 30 张图片和对应标签。

6. sample_val/
   从原始验证集抽取的 30 张图片和对应标签。

7. test_images_unlabeled/
   项目根目录 test_images 下的 6 张无标签图片，可用于预测演示。

建议发给对方时说明：
这是床位占用状态检测 YOLOv8 模型效果包，包含模型权重、训练指标、预测效果图、部分训练集和验证集样例。原项目没有独立带标签测试集，data.yaml 中 test 指向 val；另外附带 6 张无标签测试图片用于演示预测。
