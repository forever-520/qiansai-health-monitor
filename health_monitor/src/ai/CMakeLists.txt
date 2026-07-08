# health_monitor/src/ai/CMakeLists.txt
# AI 推理模块 — 构建配置
#
# 跨平台注意事项：
#   在 Windows 上测试编译时需要链接 ws2_32（本模块不依赖网络，仅备注）。
#   在 RK3588 Linux 上需要链接 librknnrt.so。
#
# 用法：
#   # RK3588 交叉编译 (NPU 后端)
#   cmake -B build -DCMAKE_TOOLCHAIN_FILE=cmake/rk3588-toolchain.cmake \
#                  -DAI_BACKEND=RKNPU
#   cmake --build build
#
#   # PC 本地编译 (SIM 模拟后端)
#   cmake -B build -DAI_BACKEND=SIM
#   cmake --build build
#
#   # 仅生成编译命令（不构建）/ compile_commands.json for IDE
#   cmake -B build -DAI_BACKEND=SIM -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
#   cmake --build build --target ai

cmake_minimum_required(VERSION 3.10)

# ---- 选项 ----
set(AI_BACKEND "SIM" CACHE STRING "AI 推理后端: SIM 或 RKNPU")
set_property(CACHE AI_BACKEND PROPERTY STRINGS SIM RKNPU)

# ---- 源文件 ----
set(AI_SOURCES
    ai_inference.c
)

# ---- 库 ----
add_library(ai STATIC ${AI_SOURCES})

target_include_directories(ai PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}
)

# ---- 后端选择 ----
if(AI_BACKEND STREQUAL "RKNPU")
    target_compile_definitions(ai PRIVATE AI_BACKEND_RKNPU)
    target_link_libraries(ai PUBLIC rknnrt)
    target_include_directories(ai PUBLIC
        ${RKNN_INCLUDE_DIR}
    )
    message(STATUS "AI 后端: RK3588 NPU (librknnrt)")
else()
    target_compile_definitions(ai PRIVATE AI_BACKEND_SIM)
    message(STATUS "AI 后端: PC 模拟模式")
endif()

# ---- 编译选项 ----
target_compile_options(ai PRIVATE
    $<$<C_COMPILER_ID:GNU>:-Wall -Wextra -Wpedantic -std=c99>
    $<$<C_COMPILER_ID:MSVC>:/W4 /std:c99>
)

# ---- 测试可执行文件 ----
option(BUILD_AI_TESTS "Build AI module test executable" OFF)
if(BUILD_AI_TESTS)
    add_executable(ai_test ai_test.c)
    target_link_libraries(ai_test ai)

    if(WIN32)
        target_link_libraries(ai_test ws2_32)
    endif()
endif()
