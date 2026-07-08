/*
 * ai_test.c — AI 推理模块单元测试
 *
 * 编译:
 *   gcc ai_test.c ai_inference.c -lm -o ai_test
 *
 * 或通过 CMake:
 *   cmake -B build -DAI_BACKEND=SIM -DBUILD_AI_TESTS=ON
 *   cmake --build build
 */

#include "ai_inference.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <time.h>

static int tests_passed = 0;
static int tests_failed = 0;

#define TEST(name, expr) do { \
    printf("  TEST: %-40s ", name); \
    if (expr) { \
        printf("[PASS]\n"); \
        tests_passed++; \
    } else { \
        printf("[FAIL]\n"); \
        tests_failed++; \
    } \
} while (0)

static void test_create_destroy(void) {
    printf("\n=== 生命周期测试 ===\n");

    /* 正常创建 SIM 后端 */
    ai_context_t *ctx = ai_create("dummy.rknn",
                                  AI_MODEL_BED_DETECT,
                                  AI_BACKEND_SIM, -1);
    TEST("创建 SIM 后端", ctx != NULL);
    TEST("后端类型 = SIM", ai_get_backend(ctx) == AI_BACKEND_SIM);
    TEST("输入宽 = 640", ai_get_input_width(ctx) == 640);
    TEST("输入高 = 640", ai_get_input_height(ctx) == 640);

    /* 设置阈值 */
    int ret = ai_set_threshold(ctx, 0.3f, 0.5f);
    TEST("设置阈值成功", ret == 0);

    /* 销毁 */
    ai_destroy(ctx);
    TEST("销毁不崩溃", 1);

    /* NULL 安全 */
    ai_destroy(NULL);
    TEST("销毁 NULL 安全", 1);

    /* stats 安全 */
    ai_stats_t stats;
    ai_get_stats(NULL, &stats);
    TEST("NULL stats 参数安全", stats.total_frames == 0);
}

static void test_result_management(void) {
    printf("\n=== 结果管理测试 ===\n");

    ai_result_t result;
    int ret = ai_result_init(&result, 10);
    TEST("初始化成功", ret == 0);
    TEST("capacity = 10", result.capacity == 10);
    TEST("detections 非 NULL", result.detections != NULL);
    TEST("初始 num_detections = 0", result.num_detections == 0);
    TEST("初始 person_in_bed = 0", result.person_in_bed == 0);

    /* 填充模拟数据 */
    if (result.capacity > 0) {
        result.detections[0].x1 = 100;
        result.detections[0].y1 = 150;
        result.detections[0].x2 = 500;
        result.detections[0].y2 = 400;
        result.detections[0].confidence = 0.95f;
        result.detections[0].class_id = 0;
        strncpy(result.detections[0].class_name, "person_in_bed",
                sizeof(result.detections[0].class_name) - 1);
        result.num_detections = 1;
        result.person_in_bed = 1;
    }

    TEST("填充数据后 num_detections = 1", result.num_detections == 1);
    TEST("确认 person_in_bed", result.person_in_bed == 1);

    /* 释放后所有指针应置空 */
    ai_result_free(&result);
    TEST("释放后 detections = NULL", result.detections == NULL);
    TEST("释放后 capacity = 0", result.capacity == 0);

    /* 重复释放安全 */
    ai_result_free(&result);
    TEST("重复释放安全", 1);
}

static void test_sim_inference(void) {
    printf("\n=== SIM 模式推理测试 ===\n");

    ai_context_t *ctx = ai_create("dummy.rknn",
                                  AI_MODEL_BED_DETECT,
                                  AI_BACKEND_SIM, -1);
    assert(ctx != NULL);

    /* 生成模拟图像数据 */
    int w = 1280, h = 720, c = 3;
    uint8_t *img = (uint8_t *)malloc(w * h * c);
    assert(img != NULL);
    memset(img, 128, w * h * c);  /* 灰色图像 */

    /* 推理 */
    ai_result_t result;
    ai_result_init(&result, 16);

    int ret = ai_run(ctx, img, w, h, c, &result);
    TEST("推理成功", ret == 0);
    TEST("推理耗时 > 0", result.inference_ms > 0);
    TEST("timestamp > 0", result.timestamp_us > 0);
    TEST("有检测结果", result.num_detections > 0);
    TEST("检测到有人", result.person_in_bed == 1);
    TEST("类名 person_in_bed",
         strcmp(result.detections[0].class_name, "person_in_bed") == 0);

    printf("      检测框: [%.0f, %.0f, %.0f, %.0f]\n",
           result.detections[0].x1, result.detections[0].y1,
           result.detections[0].x2, result.detections[0].y2);
    printf("      置信度: %.3f\n", result.detections[0].confidence);
    printf("      推理耗时: %.1f ms\n", result.inference_ms);

    /* 多次推理验证统计 */
    for (int i = 0; i < 5; i++) {
        ai_run(ctx, img, w, h, c, &result);
    }

    ai_stats_t stats;
    ai_get_stats(ctx, &stats);
    TEST("总帧数 = 6", stats.total_frames == 6);
    TEST("错误数 = 0", stats.total_errors == 0);
    TEST("平均耗时 > 0", stats.avg_inference_ms > 0);

    free(img);
    ai_result_free(&result);
    ai_destroy(ctx);
}

static void test_letterbox(void) {
    printf("\n=== Letterbox 预处理测试 ===\n");

    int src_w = 1920, src_h = 1080;
    int dst_w = 640, dst_h = 640;
    int pad_x = 0, pad_y = 0;
    int pad_color[3] = {114, 114, 114};

    uint8_t *src = (uint8_t *)malloc(src_w * src_h * 3);
    uint8_t *dst = (uint8_t *)malloc(dst_w * dst_h * 3);
    assert(src && dst);

    /* 填充源图像为已知颜色 */
    for (int y = 0; y < src_h; y++) {
        for (int x = 0; x < src_w; x++) {
            int idx = (y * src_w + x) * 3;
            src[idx + 0] = (uint8_t)(x % 256);
            src[idx + 1] = (uint8_t)(y % 256);
            src[idx + 2] = 255;
        }
    }

    float scale = ai_letterbox(src, src_w, src_h,
                               dst, dst_w, dst_h,
                               pad_color, &pad_x, &pad_y);

    TEST("缩放比 < 1", scale < 1.0f);
    TEST("缩放比 > 0", scale > 0.0f);
    TEST("pad_x >= 0", pad_x >= 0);
    TEST("pad_y >= 0", pad_y >= 0);

    /* 验证填充区域 */
    if (pad_x > 0) {
        int fill_idx = (pad_y * dst_w + 0) * 3;
        TEST("左填充色 = 114", dst[fill_idx] == 114 &&
             dst[fill_idx + 1] == 114 &&
             dst[fill_idx + 2] == 114);
    }

    /* 验证缩放后的 bbox */
    float box[4] = {100, 100, 500, 500};  /* 模型坐标空间的框 */
    ai_scale_bbox(box, src_w, src_h, dst_w, dst_h, pad_x, pad_y, scale);

    TEST("x1 >= 0", box[0] >= 0);
    TEST("y1 >= 0", box[1] >= 0);
    TEST("x2 < src_w", box[2] < src_w);
    TEST("y2 < src_h", box[3] < src_h);
    TEST("放大后 (x2-x1)/(y2-y1) 约等于源比率",
         (box[2] - box[0]) / (box[3] - box[1]) > 0.5f);

    free(src);
    free(dst);
}

static void test_error_handling(void) {
    printf("\n=== 错误处理测试 ===\n");

    /* NULL 参数 */
    TEST("ai_create(NULL) 返回 NULL", ai_create(NULL, AI_MODEL_BED_DETECT, AI_BACKEND_SIM, -1) == NULL);
    TEST("ai_destroy(NULL) 不崩溃", 1);
    TEST("ai_run(NULL, ...) 返回 -1",
         ai_run(NULL, NULL, 0, 0, 0, NULL) == -1);

    /* 无效通道 */
    ai_context_t *ctx = ai_create("dummy.rknn", AI_MODEL_BED_DETECT, AI_BACKEND_SIM, -1);
    assert(ctx != NULL);

    ai_result_t result;
    ai_result_init(&result, 8);
    uint8_t dummy[100];
    TEST("channels=1 返回 -1",
         ai_run(ctx, dummy, 10, 10, 1, &result) == -1);
    TEST("channels=4 返回 -1",
         ai_run(ctx, dummy, 10, 10, 4, &result) == -1);

    ai_result_free(&result);
    ai_destroy(ctx);
}

static void test_multiple_instances(void) {
    printf("\n=== 多实例测试 ===\n");

    ai_context_t *ctx1 = ai_create("m1.rknn", AI_MODEL_BED_DETECT, AI_BACKEND_SIM, -1);
    ai_context_t *ctx2 = ai_create("m2.rknn", AI_MODEL_BED_DETECT, AI_BACKEND_SIM, -1);
    TEST("两个实例创建成功", ctx1 != NULL && ctx2 != NULL);

    ai_result_t r1, r2;
    ai_result_init(&r1, 8);
    ai_result_init(&r2, 8);

    uint8_t img[640 * 480 * 3];
    memset(img, 128, sizeof(img));

    ai_run(ctx1, img, 640, 480, 3, &r1);
    ai_run(ctx2, img, 640, 480, 3, &r2);

    TEST("ctx1 推理成功", r1.num_detections > 0);
    TEST("ctx2 推理成功", r2.num_detections > 0);

    ai_result_free(&r1);
    ai_result_free(&r2);
    ai_destroy(ctx1);
    ai_destroy(ctx2);
}

int main(void) {
    printf("========================================\n");
    printf("  AI 推理模块 — 单元测试\n");
    printf("  后端: SIM (PC 模拟模式)\n");
    printf("========================================\n");

    srand((unsigned)time(NULL));

    test_create_destroy();
    test_result_management();
    test_sim_inference();
    test_letterbox();
    test_error_handling();
    test_multiple_instances();

    printf("\n========================================\n");
    printf("  结果: %d 通过, %d 失败 / %d 总测试\n",
           tests_passed, tests_failed, tests_passed + tests_failed);
    printf("========================================\n");

    return (tests_failed > 0) ? 1 : 0;
}
