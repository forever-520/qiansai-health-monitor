/*
 * ai_inference.c — AI 推理核心实现
 *
 * 采用后端-前端分离架构：
 *   前端 (本文件)       : API 调度、预处理、后处理、结果管理
 *   后端 (条件编译)     :
 *     - AI_BACKEND_RKNPU : RK3588 NPU 推理 (librknnrt)
 *     - AI_BACKEND_SIM   : PC 模拟模式 (返回模拟结果)
 *
 * YOLOv8 输出格式说明 (1 class):
 *   [tx, ty, tw, th, obj_conf, cls_conf] × 8400 anchors
 *   YOLOv8 使用解耦头，输出为 1×84×8400 (84 = 4 bbox + 80 coco 或 1 class + ...)
 *   对于单类模型 (person_in_bed)，输出为 1×6×8400 (6 = 4 bbox + 1 obj + 1 cls)
 *
 * 编译:
 *   # RK3588 NPU 后端
 *   gcc -DAI_BACKEND_RKNPU ai_inference.c -lrknnrt -lm -o ai_inference
 *
 *   # PC 模拟模式 (默认)
 *   gcc ai_inference.c -lm -o ai_inference
 */

#include "ai_inference.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>

#ifdef _WIN32
    #define WIN32_LEAN_AND_MEAN
    #include <windows.h>
    static uint64_t ai_timestamp(void) {
        return (uint64_t)GetTickCount() * 1000;
    }
#else
    #include <sys/time.h>
    static uint64_t ai_timestamp(void) {
        struct timeval tv;
        gettimeofday(&tv, NULL);
        return (uint64_t)tv.tv_sec * 1000000 + (uint64_t)tv.tv_usec;
    }
#endif

/* ========================================================================
 * RKNN 后端 (条件编译)
 * ======================================================================*/
#ifdef AI_BACKEND_RKNPU
    #include <rknn_api.h>

    typedef struct {
        rknn_context  ctx;
        rknn_input_output_num io_num;
        rknn_tensor_attr *input_attrs;
        rknn_tensor_attr *output_attrs;
        int npu_core;
    } rknn_backend_t;

    static int rknn_backend_create(ai_context_t *ctx,
                                   const char *model_path,
                                   int npu_core);
    static int rknn_backend_run(ai_context_t *ctx,
                                const uint8_t *input_data,
                                int input_size);
    static void rknn_backend_destroy(ai_context_t *ctx);
#endif /* AI_BACKEND_RKNPU */

/* ========================================================================
 * 内部结构定义
 * ======================================================================*/
#define AI_MAX_DETECTIONS  32
#define AI_MODEL_INPUT_W   640
#define AI_MODEL_INPUT_H   640
#define AI_ANCHOR_COUNT    8400   /* YOLOv8 输出锚点数量 */

struct ai_context {
    ai_model_type_t model_type;
    ai_backend_t    backend;

    /* 模型信息 */
    char   model_path[512];
    int    input_width;
    int    input_height;

    /* 阈值 */
    float  conf_thresh;
    float  iou_thresh;

    /* 统计 */
    uint64_t total_frames;
    uint64_t total_inference_us;
    uint64_t total_errors;

    /* 后端特定数据 */
    void *backend_data;

    /* 内部工作缓冲区 (预处理用) */
    uint8_t *work_buf;
    int      work_buf_size;
};

/* ========================================================================
 * YOLOv8 后处理
 * ======================================================================*/

/*
 * sigmoid 函数
 */
static inline float ai_sigmoid(float x) {
    return 1.0f / (1.0f + expf(-x));
}

/*
 * 两个框的 IoU 计算
 */
static float ai_iou(const float a[4], const float b[4]) {
    float inter_x1 = fmaxf(a[0], b[0]);
    float inter_y1 = fmaxf(a[1], b[1]);
    float inter_x2 = fminf(a[2], b[2]);
    float inter_y2 = fminf(a[3], b[3]);

    float inter_area = fmaxf(0, inter_x2 - inter_x1) *
                       fmaxf(0, inter_y2 - inter_y1);
    float area_a = (a[2] - a[0]) * (a[3] - a[1]);
    float area_b = (b[2] - b[0]) * (b[3] - b[1]);
    float union_area = area_a + area_b - inter_area;

    return (union_area > 0) ? inter_area / union_area : 0;
}

/*
 * NMS (非极大值抑制) - 贪心算法
 *
 * dets:    检测框数组 [N][4] (x1,y1,x2,y2)
 * scores:  置信度数组 [N]
 * n:       数组长度
 * iou_thr: IoU 阈值
 * out:     输出保留的索引
 * out_n:   输出数量
 */
static void ai_nms(const float (*dets)[4], const float *scores, int n,
                   float iou_thr, int *out, int *out_n) {
    int *order = (int *)malloc(n * sizeof(int));
    int *keep = (int *)malloc(n * sizeof(int));
    int *suppressed = (int *)calloc(n, sizeof(int));

    if (!order || !keep || !suppressed) {
        free(order); free(keep); free(suppressed);
        *out_n = 0;
        return;
    }

    /* 按置信度降序排序索引 */
    for (int i = 0; i < n; i++) order[i] = i;
    for (int i = 0; i < n - 1; i++) {
        for (int j = i + 1; j < n; j++) {
            if (scores[order[j]] > scores[order[i]]) {
                int tmp = order[i];
                order[i] = order[j];
                order[j] = tmp;
            }
        }
    }

    int count = 0;
    for (int i = 0; i < n; i++) {
        if (suppressed[order[i]]) continue;
        keep[count++] = order[i];

        for (int j = i + 1; j < n; j++) {
            if (suppressed[order[j]]) continue;
            if (ai_iou(dets[order[i]], dets[order[j]]) > iou_thr) {
                suppressed[order[j]] = 1;
            }
        }
    }

    memcpy(out, keep, count * sizeof(int));
    *out_n = count;

    free(order);
    free(keep);
    free(suppressed);
}

/*
 * ai_yolo_decode — 解析 YOLOv8 原始输出
 *
 * output:      RKNN 原始输出张量数据 (float32)
 * output_size: 输出数据长度 (float 个数)
 * mode_w:      模型输入宽度 (640)
 * model_h:     模型输入高度 (640)
 * num_classes: 类别数 (1 或 80)
 * conf_thr:    置信度阈值
 * dets:        输出检测框数组 [max_dets][4]
 * scores:      输出置信度数组 [max_dets]
 * classes:     输出类别 ID 数组 [max_dets]
 * max_dets:    输出数组容量
 *
 * 返回: 检测到的目标数量
 *
 * YOLOv8 输出布局:
 *   对于单类模型: [tx, ty, tw, th, cls_conf] × 8400
 *   对于多类模型: [tx, ty, tw, th, cls1_conf, cls2_conf, ...] × 8400
 *
 * 注意: 不同 RKNN 转换工具的 YOLOv8 输出格式可能有差异。
 *       rknn-toolkit2 导出的模型输出为 1×N×8400 (N = 4 + num_classes)。
 *       本实现支持两种常见布局:
 *       - 紧凑型 (6 通道): tx, ty, tw, th, obj_conf, cls_conf (单类)
 *       - 标准型 (84 通道): tx, ty, tw, th, cls0~cls79 (80 类 COCO)
 */
static int ai_yolo_decode(const float *output, int output_size,
                          int model_w, int model_h,
                          int num_classes, float conf_thr,
                          float (*dets)[4], float *scores, int *classes,
                          int max_dets) {
    int channels = output_size / AI_ANCHOR_COUNT;
    int detected = 0;

    for (int i = 0; i < AI_ANCHOR_COUNT && detected < max_dets; i++) {
        float *anchor = (float *)output + i * channels;

        float obj_conf;
        float cls_conf;
        int cls_id;

        if (channels == 6) {
            /* 紧凑型: [tx, ty, tw, th, obj_conf, cls_conf] */
            obj_conf = ai_sigmoid(anchor[4]);
            cls_conf = ai_sigmoid(anchor[5]);
            cls_id = 0;
        } else if (channels >= 5 + num_classes) {
            /* 标准型: [tx, ty, tw, th, cls0, cls1, ...] */
            obj_conf = 1.0f;
            cls_conf = 0;
            cls_id = 0;
            for (int j = 0; j < num_classes; j++) {
                float c = ai_sigmoid(anchor[4 + j]);
                if (c > cls_conf) {
                    cls_conf = c;
                    cls_id = j;
                }
            }
        } else {
            continue; /* 未知格式 */
        }

        float conf = obj_conf * cls_conf;
        if (conf < conf_thr) continue;

        /* YOLOv8 输出框是 [cx, cy, w, h] 归一化到 640x640 */
        float cx = anchor[0];
        float cy = anchor[1];
        float w  = anchor[2];
        float h  = anchor[3];

        /* 转成 [x1, y1, x2, y2] (归一化坐标，后续再缩放) */
        dets[detected][0] = (cx - w / 2.0f);
        dets[detected][1] = (cy - h / 2.0f);
        dets[detected][2] = (cx + w / 2.0f);
        dets[detected][3] = (cy + h / 2.0f);

        scores[detected] = conf;
        classes[detected] = cls_id;
        detected++;
    }

    return detected;
}

/*
 * ai_yolo_postprocess — 完整 YOLOv8 后处理管线
 *
 * output:          RKNN 原始输出
 * output_size:     输出数据长度 (float 个数)
 * num_classes:     类别数
 * model_w/h:       模型输入尺寸
 * pad_x/pad_y:     letterbox 填充偏移
 * scale:           letterbox 缩放比例
 * src_w/h:         原始图像尺寸
 * conf_thr/iou_thr: 阈值
 * result:          输出结果
 *
 * 流程: 解码 -> NMS -> 坐标缩放 -> 填充 result
 */
static void ai_yolo_postprocess(const float *output, int output_size,
                                int num_classes,
                                int model_w, int model_h,
                                int pad_x, int pad_y, float scale,
                                int src_w, int src_h,
                                float conf_thr, float iou_thr,
                                ai_result_t *result) {
    /* 临时缓冲区 */
    float (*dets)[4] = (float (*)[4])malloc(AI_MAX_DETECTIONS * 4 * sizeof(float));
    float *scores = (float *)malloc(AI_MAX_DETECTIONS * sizeof(float));
    int *classes = (int *)malloc(AI_MAX_DETECTIONS * sizeof(int));

    if (!dets || !scores || !classes) {
        free(dets); free(scores); free(classes);
        return;
    }

    /* 第一步: 解码原始输出 */
    int n = ai_yolo_decode(output, output_size,
                           model_w, model_h,
                           num_classes, conf_thr,
                           dets, scores, classes,
                           AI_MAX_DETECTIONS);

    /* 第二步: NMS */
    int *keep = (int *)malloc(AI_MAX_DETECTIONS * sizeof(int));
    int keep_n = 0;

    if (keep && n > 0) {
        float (*dets_n)[4] = (float (*)[4])malloc(n * 4 * sizeof(float));
        float *scores_n = (float *)malloc(n * sizeof(float));
        if (dets_n && scores_n) {
            memcpy(dets_n, dets, n * 4 * sizeof(float));
            memcpy(scores_n, scores, n * sizeof(float));
            ai_nms(dets_n, scores_n, n, iou_thr, keep, &keep_n);
            free(dets_n);
            free(scores_n);
        }
    }

    /* 第三步: 填充结果 */
    result->num_detections = 0;
    result->person_in_bed = 0;
    result->top_confidence = 0;

    for (int i = 0; i < keep_n && result->num_detections < result->capacity; i++) {
        int idx = keep[i];
        ai_detection_t *d = &result->detections[result->num_detections];

        /* 将模型坐标空间的框映射回原始图像 */
        float x1 = (dets[idx][0] - pad_x) / scale;
        float y1 = (dets[idx][1] - pad_y) / scale;
        float x2 = (dets[idx][2] - pad_x) / scale;
        float y2 = (dets[idx][3] - pad_y) / scale;

        /* 裁剪到图像边界 */
        d->x1 = fmaxf(0, fminf(x1, src_w - 1));
        d->y1 = fmaxf(0, fminf(y1, src_h - 1));
        d->x2 = fmaxf(0, fminf(x2, src_w - 1));
        d->y2 = fmaxf(0, fminf(y2, src_h - 1));
        d->confidence = scores[idx];
        d->class_id = classes[idx];

        const char *name = (classes[idx] == 0) ? "person_in_bed" : "unknown";
        strncpy(d->class_name, name, sizeof(d->class_name) - 1);
        d->class_name[sizeof(d->class_name) - 1] = '\0';

        if (d->confidence > result->top_confidence) {
            result->top_confidence = d->confidence;
        }

        result->num_detections++;
    }

    /* 判断是否有人 */
    for (int i = 0; i < result->num_detections; i++) {
        if (result->detections[i].class_id == 0 &&
            result->detections[i].confidence >= conf_thr) {
            result->person_in_bed = 1;
            break;
        }
    }

    free(dets);
    free(scores);
    free(classes);
    free(keep);
}

/* ========================================================================
 * 图像预处理
 * ======================================================================*/

float ai_letterbox(const uint8_t *src, int src_w, int src_h,
                   uint8_t *dst, int dst_w, int dst_h,
                   int pad_color[3],
                   int *pad_x, int *pad_y) {
    float scale = fminf((float)dst_w / src_w, (float)dst_h / src_h);
    int new_w = (int)(src_w * scale);
    int new_h = (int)(src_h * scale);

    int dx = (dst_w - new_w) / 2;
    int dy = (dst_h - new_h) / 2;

    if (pad_x) *pad_x = dx;
    if (pad_y) *pad_y = dy;

    /* 填充整个目标区域为背景色 */
    int r = (pad_color) ? pad_color[0] : 114;
    int g = (pad_color) ? pad_color[1] : 114;
    int b = (pad_color) ? pad_color[2] : 114;

    for (int y = 0; y < dst_h; y++) {
        for (int x = 0; x < dst_w; x++) {
            int idx = (y * dst_w + x) * 3;
            dst[idx + 0] = (uint8_t)r;
            dst[idx + 1] = (uint8_t)g;
            dst[idx + 2] = (uint8_t)b;
        }
    }

    /* 等比例缩放复制源图像到目标中心 */
    if (new_w > 0 && new_h > 0) {
        for (int y = 0; y < new_h; y++) {
            int src_y = (int)((float)y / scale);
            if (src_y >= src_h) src_y = src_h - 1;
            for (int x = 0; x < new_w; x++) {
                int src_x = (int)((float)x / scale);
                if (src_x >= src_w) src_x = src_w - 1;
                int src_idx = (src_y * src_w + src_x) * 3;
                int dst_idx = ((dy + y) * dst_w + (dx + x)) * 3;
                dst[dst_idx + 0] = src[src_idx + 0];
                dst[dst_idx + 1] = src[src_idx + 1];
                dst[dst_idx + 2] = src[src_idx + 2];
            }
        }
    }

    return scale;
}

void ai_scale_bbox(float box[4], int src_w, int src_h,
                   int model_w, int model_h,
                   int pad_x, int pad_y, float scale) {
    box[0] = (box[0] - pad_x) / scale;
    box[1] = (box[1] - pad_y) / scale;
    box[2] = (box[2] - pad_x) / scale;
    box[3] = (box[3] - pad_y) / scale;

    box[0] = fmaxf(0, fminf(box[0], src_w - 1));
    box[1] = fmaxf(0, fminf(box[1], src_h - 1));
    box[2] = fmaxf(0, fminf(box[2], src_w - 1));
    box[3] = fmaxf(0, fminf(box[3], src_h - 1));
}

/* ========================================================================
 * SIM 后端 (PC 模拟模式)
 * ======================================================================*/
typedef struct {
    int active; /* 始终为 1，表示 sim 模式已就绪 */
} sim_backend_t;

/*
 * 在 SIM 模式下生成模拟检测结果（模拟真实检测场景）
 */
static int sim_run(ai_context_t *ctx,
                   const uint8_t *image_data,
                   int width, int height,
                   ai_result_t *result) {
    (void)image_data;

    /* 模拟推理耗时 */
    uint64_t start = ai_timestamp();

    /* 模拟延迟: 随机 10~30ms (模拟 NPU 推理延迟) */
    uint64_t delay_us = 10000 + (uint64_t)(rand() % 20000);
#ifdef _WIN32
    Sleep((DWORD)(delay_us / 1000));
#else
    struct timespec ts = {
        .tv_sec = delay_us / 1000000,
        .tv_nsec = (delay_us % 1000000) * 1000
    };
    nanosleep(&ts, NULL);
#endif

    /* 生成模拟检测结果 */
    result->num_detections = 0;
    result->person_in_bed = 1;  /* 默认模拟为有人 */
    result->top_confidence = 0.93f;

    /* 模拟一个检测框 (床铺区域) */
    if (result->capacity > 0) {
        ai_detection_t *d = &result->detections[0];
        d->x1 = width * 0.1f;
        d->y1 = height * 0.15f;
        d->x2 = width * 0.85f;
        d->y2 = height * 0.88f;
        d->confidence = 0.93f;
        d->class_id = 0;
        strncpy(d->class_name, "person_in_bed", sizeof(d->class_name) - 1);
        d->class_name[sizeof(d->class_name) - 1] = '\0';
        result->num_detections = 1;
    }

    result->inference_ms = (double)(ai_timestamp() - start) / 1000.0;
    result->timestamp_us = ai_timestamp();

    return 0;
}

/* ========================================================================
 * 公共 API 实现
 * ======================================================================*/

ai_context_t *ai_create(const char *model_path,
                        ai_model_type_t type,
                        ai_backend_t backend,
                        int npu_core) {
    if (!model_path) return NULL;

    ai_context_t *ctx = (ai_context_t *)calloc(1, sizeof(ai_context_t));
    if (!ctx) return NULL;

    strncpy(ctx->model_path, model_path, sizeof(ctx->model_path) - 1);
    ctx->model_type = type;
    ctx->input_width = AI_MODEL_INPUT_W;
    ctx->input_height = AI_MODEL_INPUT_H;
    ctx->conf_thresh = 0.25f;
    ctx->iou_thresh = 0.45f;
    ctx->total_frames = 0;
    ctx->total_inference_us = 0;
    ctx->total_errors = 0;

    /* 预分配工作缓冲区 */
    ctx->work_buf_size = ctx->input_width * ctx->input_height * 3;
    ctx->work_buf = (uint8_t *)malloc(ctx->work_buf_size);
    if (!ctx->work_buf) {
        free(ctx);
        return NULL;
    }

    /* 选择后端 */
    ctx->backend = backend;
    if (ctx->backend == AI_BACKEND_AUTO) {
#ifdef AI_BACKEND_RKNPU
        ctx->backend = AI_BACKEND_RKNPU;
#else
        ctx->backend = AI_BACKEND_SIM;
#endif
    }

    int ret = 0;

    switch (ctx->backend) {
#ifdef AI_BACKEND_RKNPU
    case AI_BACKEND_RKNPU:
        ret = rknn_backend_create(ctx, model_path, npu_core);
        break;
#endif
    case AI_BACKEND_SIM: {
        sim_backend_t *sim = (sim_backend_t *)calloc(1, sizeof(sim_backend_t));
        if (sim) {
            sim->active = 1;
            ctx->backend_data = sim;
            ret = 0;
        } else {
            ret = -1;
        }
        break;
    }
    default:
        ret = -1;
        break;
    }

    if (ret != 0) {
        free(ctx->work_buf);
        free(ctx);
        return NULL;
    }

    return ctx;
}

void ai_destroy(ai_context_t *ctx) {
    if (!ctx) return;

    switch (ctx->backend) {
#ifdef AI_BACKEND_RKNPU
    case AI_BACKEND_RKNPU:
        rknn_backend_destroy(ctx);
        break;
#endif
    case AI_BACKEND_SIM:
        free(ctx->backend_data);
        break;
    default:
        break;
    }

    free(ctx->work_buf);
    free(ctx);
}

void ai_reset(ai_context_t *ctx) {
    if (!ctx) return;
    ctx->total_frames = 0;
    ctx->total_inference_us = 0;
    ctx->total_errors = 0;
}

int ai_set_threshold(ai_context_t *ctx, float conf_thresh, float iou_thresh) {
    if (!ctx) return -1;
    ctx->conf_thresh = conf_thresh;
    ctx->iou_thresh = iou_thresh;
    return 0;
}

int ai_set_input_size(ai_context_t *ctx, int width, int height) {
    if (!ctx || width <= 0 || height <= 0) return -1;

    ctx->input_width = width;
    ctx->input_height = height;

    /* 重新分配工作缓冲区 */
    int new_size = width * height * 3;
    uint8_t *new_buf = (uint8_t *)realloc(ctx->work_buf, new_size);
    if (!new_buf && new_size > 0) return -1;

    ctx->work_buf = new_buf;
    ctx->work_buf_size = new_size;
    return 0;
}

int ai_run(ai_context_t *ctx,
           const uint8_t *image_data,
           int width, int height, int channels,
           ai_result_t *result) {
    if (!ctx || !image_data || !result) return -1;
    if (channels != 3) return -1;

    uint64_t t_start = ai_timestamp();

    /* ---- 预处理: letterbox ---- */
    int pad_x, pad_y;
    float scale = ai_letterbox(image_data, width, height,
                               ctx->work_buf,
                               ctx->input_width, ctx->input_height,
                               NULL, &pad_x, &pad_y);

    /* ---- 推理 ---- */
    int infer_ret = 0;

    switch (ctx->backend) {
#ifdef AI_BACKEND_RKNPU
    case AI_BACKEND_RKNPU:
        infer_ret = rknn_backend_run(ctx, ctx->work_buf,
                                     ctx->input_width * ctx->input_height * 3);
        if (infer_ret == 0) {
            /* 从 RKNN 后端获取输出并后处理 */
            rknn_backend_t *r = (rknn_backend_t *)ctx->backend_data;
            if (r && r->io_num.n_output > 0) {
                rknn_output outputs[4];
                int ret_get = rknn_outputs_get(r->ctx, r->io_num.n_output,
                                               outputs, NULL);
                if (ret_get == 0) {
                    int num_classes = (ctx->model_type == AI_MODEL_BED_DETECT) ? 1 : 80;
                    int output_floats = outputs[0].size / sizeof(float);

                    ai_yolo_postprocess((const float *)outputs[0].buf,
                                        output_floats,
                                        num_classes,
                                        ctx->input_width, ctx->input_height,
                                        pad_x, pad_y, scale,
                                        width, height,
                                        ctx->conf_thresh, ctx->iou_thresh,
                                        result);

                    rknn_outputs_release(r->ctx, r->io_num.n_output, outputs);
                } else {
                    infer_ret = -1;
                }
            }
        }
        break;
#endif
    case AI_BACKEND_SIM:
        infer_ret = sim_run(ctx, image_data, width, height, result);
        break;
    default:
        infer_ret = -1;
        break;
    }

    uint64_t t_end = ai_timestamp();

    /* ---- 更新统计 ---- */
    ctx->total_frames++;
    if (infer_ret == 0) {
        ctx->total_inference_us += (t_end - t_start);
        result->inference_ms = (double)(t_end - t_start) / 1000.0;
        result->timestamp_us = t_end;
    } else {
        ctx->total_errors++;
        result->inference_ms = 0;
        result->num_detections = 0;
        result->person_in_bed = 0;
    }

    return infer_ret;
}

int ai_run_from_file(ai_context_t *ctx,
                     const char *image_path,
                     ai_result_t *result) {
    if (!ctx || !image_path || !result) return -1;

    /*
     * 文件推理仅在 SIM 模式下支持。
     * RKNPU 模式下需要用相机或其他方式获取图像数据。
     */
    if (ctx->backend == AI_BACKEND_SIM) {
        /* SIM 模式下直接返回模拟结果，跳过文件读取 */
        return sim_run(ctx, NULL, 640, 480, result);
    }

    return -1;
}

/* ===== 结果管理 ===== */

int ai_result_init(ai_result_t *result, int max_detections) {
    if (!result) return -1;
    memset(result, 0, sizeof(ai_result_t));

    result->capacity = (max_detections > 0) ? max_detections : AI_MAX_DETECTIONS;
    result->detections = (ai_detection_t *)calloc(result->capacity,
                                                  sizeof(ai_detection_t));
    if (!result->detections) {
        result->capacity = 0;
        return -1;
    }

    /* 姿态预留 */
    result->poses = NULL;
    result->num_poses = 0;

    return 0;
}

void ai_result_free(ai_result_t *result) {
    if (!result) return;
    free(result->detections);
    free(result->poses);
    memset(result, 0, sizeof(ai_result_t));
}

/* ===== 信息查询 ===== */

int ai_get_input_width(const ai_context_t *ctx) {
    return ctx ? ctx->input_width : 0;
}

int ai_get_input_height(const ai_context_t *ctx) {
    return ctx ? ctx->input_height : 0;
}

ai_backend_t ai_get_backend(const ai_context_t *ctx) {
    return ctx ? ctx->backend : AI_BACKEND_SIM;
}

void ai_get_stats(const ai_context_t *ctx, ai_stats_t *stats) {
    if (!ctx || !stats) {
        if (stats) memset(stats, 0, sizeof(*stats));
        return;
    }
    stats->total_frames = ctx->total_frames;
    stats->total_inference_us = ctx->total_inference_us;
    stats->total_errors = ctx->total_errors;
    stats->avg_inference_ms = (ctx->total_frames > 0)
        ? (double)ctx->total_inference_us / ctx->total_frames / 1000.0
        : 0.0;
}

/* ========================================================================
 * RKNN 后端实现 (条件编译)
 * ======================================================================*/
#ifdef AI_BACKEND_RKNPU

static int rknn_backend_create(ai_context_t *ctx,
                               const char *model_path,
                               int npu_core) {
    rknn_backend_t *r = (rknn_backend_t *)calloc(1, sizeof(rknn_backend_t));
    if (!r) return -1;

    r->npu_core = (npu_core >= 0 && npu_core <= 2) ? npu_core : 0;

    /* 读取模型文件到内存 */
    FILE *fp = fopen(model_path, "rb");
    if (!fp) {
        fprintf(stderr, "[AI] 无法打开模型文件: %s\n", model_path);
        free(r);
        return -1;
    }
    fseek(fp, 0, SEEK_END);
    long model_size = ftell(fp);
    fseek(fp, 0, SEEK_SET);

    void *model_data = malloc(model_size);
    if (!model_data) {
        fclose(fp);
        free(r);
        return -1;
    }
    size_t read_bytes = fread(model_data, 1, model_size, fp);
    fclose(fp);

    if ((long)read_bytes != model_size) {
        free(model_data);
        free(r);
        return -1;
    }

    /* 初始化 RKNN */
    rknn_init_extended extended_cfg;
    memset(&extended_cfg, 0, sizeof(extended_cfg));
    extended_cfg.num_cores = r->npu_core;

    int ret = rknn_init(&r->ctx, model_data, model_size,
                        RKNN_FLAG_ASYNC_MASK, &extended_cfg);
    free(model_data);

    if (ret < 0) {
        fprintf(stderr, "[AI] rknn_init 失败: %d\n", ret);
        free(r);
        return -1;
    }

    /* 查询输入输出信息 */
    ret = rknn_query(r->ctx, RKNN_QUERY_INPUT_OUTPUT_NUM,
                     &r->io_num, sizeof(r->io_num));
    if (ret < 0) {
        fprintf(stderr, "[AI] rknn_query IO_NUM 失败: %d\n", ret);
        rknn_destroy(r->ctx);
        free(r);
        return -1;
    }

    /* 查询输入属性 */
    r->input_attrs = (rknn_tensor_attr *)calloc(
        r->io_num.n_input, sizeof(rknn_tensor_attr));
    for (uint32_t i = 0; i < r->io_num.n_input; i++) {
        r->input_attrs[i].index = i;
        rknn_query(r->ctx, RKNN_QUERY_INPUT_ATTR,
                   &r->input_attrs[i], sizeof(rknn_tensor_attr));
    }

    /* 查询输出属性 */
    r->output_attrs = (rknn_tensor_attr *)calloc(
        r->io_num.n_output, sizeof(rknn_tensor_attr));
    for (uint32_t i = 0; i < r->io_num.n_output; i++) {
        r->output_attrs[i].index = i;
        rknn_query(r->ctx, RKNN_QUERY_OUTPUT_ATTR,
                   &r->output_attrs[i], sizeof(rknn_tensor_attr));
    }

    ctx->backend_data = r;

    fprintf(stdout, "[AI] RKNN 模型加载成功: %s\n"
            "     输入: %d 个, 输出: %d 个, NPU 核心: %d\n",
            model_path, r->io_num.n_input, r->io_num.n_output, r->npu_core);

    return 0;
}

static int rknn_backend_run(ai_context_t *ctx,
                            const uint8_t *input_data,
                            int input_size) {
    rknn_backend_t *r = (rknn_backend_t *)ctx->backend_data;
    if (!r) return -1;

    /* 设置输入 */
    rknn_input inputs[1];
    memset(inputs, 0, sizeof(inputs));
    inputs[0].index = 0;
    inputs[0].type = RKNN_TENSOR_UINT8;
    inputs[0].fmt = RKNN_TENSOR_NHWC;
    inputs[0].buf = input_data;
    inputs[0].size = (uint32_t)input_size;

    int ret = rknn_inputs_set(r->ctx, 1, inputs);
    if (ret < 0) return ret;

    /* 执行推理 */
    ret = rknn_run(r->ctx, NULL);
    if (ret < 0) return ret;

    return 0;
}

static void rknn_backend_destroy(ai_context_t *ctx) {
    rknn_backend_t *r = (rknn_backend_t *)ctx->backend_data;
    if (!r) return;

    rknn_destroy(r->ctx);
    free(r->input_attrs);
    free(r->output_attrs);
    free(r);
    ctx->backend_data = NULL;
}

#endif /* AI_BACKEND_RKNPU */
