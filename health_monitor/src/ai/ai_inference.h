/*
 * ai_inference.h — AI 推理核心 API (RK3588 NPU / PC 模拟)
 *
 * 面向 health_monitor 项目的统一推理接口：
 *   - 床上人体检测 (YOLOv8, 1 class: person_in_bed)
 *   - 人体姿态估计 (YOLOv8-Pose, 17 keypoints) [预留]
 *   - 跌倒检测 [预留]
 *
 * 使用方式:
 *   1. ai_create() 创建上下文（指定模型路径与类型）
 *   2. 每帧调用 ai_run() 传入图像数据
 *   3. 读取 ai_result_t 获取检测结果
 *   4. ai_destroy() 释放
 *
 * 后端选择:
 *   - AI_BACKEND_RKNPU: RK3588 NPU (通过 RKNN C API 推理)
 *   - AI_BACKEND_SIM: PC 模拟模式 (返回模拟数据，用于开发调试)
 *
 * 集成到 health_monitor:
 *   AI 推理层由 A76 核心管理，结果通过共享内存传递给融合层。
 *   多模态融合逻辑见 multi-modal-fusion-architecture.md。
 */

#ifndef AI_INFERENCE_H
#define AI_INFERENCE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ===== 模型类型 ===== */
typedef enum {
    AI_MODEL_BED_DETECT = 0,   /* YOLOv8 床上人体检测 (单类) */
    AI_MODEL_POSE,              /* YOLOv8-Pose 人体关键点 [预留] */
    AI_MODEL_FALL_DETECT,       /* 跌倒检测 [预留] */
} ai_model_type_t;

/* ===== 推理后端 ===== */
typedef enum {
    AI_BACKEND_AUTO = 0,        /* 自动选择（RK3588 上使用 NPU，否则 SIM） */
    AI_BACKEND_RKNPU,           /* RK3588 NPU (RKNN C API) */
    AI_BACKEND_SIM,             /* PC 模拟模式 */
} ai_backend_t;

/* ===== 单目标检测结果 ===== */
typedef struct {
    float  x1, y1, x2, y2;     /* 检测框 (像素坐标，原始图像尺寸) */
    float  confidence;          /* 置信度 (0.0 ~ 1.0) */
    int    class_id;            /* 类别 ID */
    char   class_name[32];      /* 类别名称 */
} ai_detection_t;

/* ===== 关键点 [预留] ===== */
typedef struct {
    float x, y;                 /* 坐标 (像素) */
    float confidence;           /* 置信度 */
} ai_keypoint_t;

#define AI_MAX_KEYPOINTS 17     /* COCO 17 关键点 */

/* ===== 姿态估计结果 [预留] ===== */
typedef struct {
    ai_keypoint_t keypoints[AI_MAX_KEYPOINTS];
    float         confidence;   /* 实例置信度 */
    float         bbox[4];      /* x1, y1, x2, y2 */
} ai_pose_t;

/* ===== 单帧推理结果 ===== */
typedef struct {
    /* 检测结果 */
    ai_detection_t *detections;
    int             num_detections;
    int             capacity;

    /* 业务抽象 */
    int   person_in_bed;        /* 0/1: 床上是否有人 */
    float top_confidence;       /* 最高置信度 */

    /* 姿态 [预留] */
    ai_pose_t *poses;
    int        num_poses;

    /* 性能 */
    double inference_ms;        /* 推理耗时 (ms) */
    uint64_t timestamp_us;      /* 时间戳 (微秒) */

    /* 内部 */
    int _reserved[8];
} ai_result_t;

/* ===== AI 上下文（不透明指针） ===== */
typedef struct ai_context ai_context_t;

/* ===== 生命周期 API ===== */

/*
 * ai_create — 创建推理上下文
 *
 * model_path: RKNN 模型文件路径 (.rknn)
 * type:       模型类型 (AI_MODEL_BED_DETECT 等)
 * backend:    推理后端 (AI_BACKEND_AUTO / AI_BACKEND_RKNPU / AI_BACKEND_SIM)
 * npu_core:   NPU 核心编号 (0-2, 仅 RKNPU 后端有效, -1 表示自动)
 *
 * 返回: 上下文指针，失败返回 NULL
 */
ai_context_t *ai_create(const char *model_path,
                        ai_model_type_t type,
                        ai_backend_t backend,
                        int npu_core);

/*
 * ai_destroy — 销毁推理上下文，释放所有资源
 */
void ai_destroy(ai_context_t *ctx);

/*
 * ai_reset — 重置推理状态（模型推理前后调用，清理结果缓存）
 */
void ai_reset(ai_context_t *ctx);

/* ===== 配置 API ===== */

/*
 * ai_set_threshold — 设置置信度和 NMS 阈值
 *
 * conf_thresh: 置信度阈值 (默认 0.25)
 * iou_thresh:  NMS 的 IoU 阈值 (默认 0.45)
 *
 * 返回: 0 成功，-1 失败
 */
int ai_set_threshold(ai_context_t *ctx, float conf_thresh, float iou_thresh);

/*
 * ai_set_input_size — 设置模型输入尺寸
 *
 * 默认 640x640 (YOLOv8 标准输入)。当使用不同分辨率模型时调用。
 *
 * 返回: 0 成功，-1 失败
 */
int ai_set_input_size(ai_context_t *ctx, int width, int height);

/* ===== 推理 API ===== */

/*
 * ai_run — 执行单帧推理
 *
 * image_data: 图像数据 (RGB/BGR, 连续排布)
 * width:      图像宽度 (像素)
 * height:     图像高度 (像素)
 * channels:   通道数 (3)
 * result:     输出结果结构体，由调用者预分配，ai_run 填充数据
 *
 * 返回: 0 成功，-1 失败
 */
int ai_run(ai_context_t *ctx,
           const uint8_t *image_data,
           int width, int height, int channels,
           ai_result_t *result);

/*
 * ai_run_from_file — 从图片文件推理 (PC 调试用)
 *
 * image_path: 图片文件路径
 * result:     输出结果
 *
 * 返回: 0 成功，-1 失败
 */
int ai_run_from_file(ai_context_t *ctx,
                     const char *image_path,
                     ai_result_t *result);

/* ===== 结果管理 ===== */

/*
 * ai_result_init — 初始化结果结构体（分配内部缓冲区）
 *
 * 返回: 0 成功，-1 失败
 */
int ai_result_init(ai_result_t *result, int max_detections);

/*
 * ai_result_free — 释放结果结构体的内部资源
 */
void ai_result_free(ai_result_t *result);

/* ===== 信息查询 ===== */

/* 获取模型输入尺寸 */
int ai_get_input_width(const ai_context_t *ctx);
int ai_get_input_height(const ai_context_t *ctx);

/* 获取当前后端类型 */
ai_backend_t ai_get_backend(const ai_context_t *ctx);

/* 获取推理统计信息 */
typedef struct {
    uint64_t total_frames;
    uint64_t total_inference_us;
    double   avg_inference_ms;
    uint64_t total_errors;
} ai_stats_t;

void ai_get_stats(const ai_context_t *ctx, ai_stats_t *stats);

/* ===== 图像预处理工具 ===== */

/*
 * ai_letterbox — 等比例缩放 + 填充，使图像适配模型输入尺寸
 *
 * src:        源图像 (RGB/BGR, HWC)
 * src_w/h:    源图像尺寸
 * dst:        输出缓冲区 (大小 = dst_w * dst_h * 3)
 * dst_w/h:    目标尺寸 (通常为模型输入尺寸)
 * pad_color:  填充颜色 (RGB, 默认 114,114,114)
 *
 * 返回: 缩放比例因子，以及填充偏移量 (通过参数返回)
 */
float ai_letterbox(const uint8_t *src, int src_w, int src_h,
                   uint8_t *dst, int dst_w, int dst_h,
                   int pad_color[3],
                   int *pad_x, int *pad_y);

/*
 * ai_scale_bbox — 将模型输出框映射回原始图像坐标
 *
 * 模型输出框是 640x640 输入空间中的坐标，
 * 需要根据 letterbox 的参数反算回原始图像。
 */
void ai_scale_bbox(float box[4], int src_w, int src_h,
                   int model_w, int model_h,
                   int pad_x, int pad_y, float scale);

#ifdef __cplusplus
}
#endif

#endif /* AI_INFERENCE_H */
