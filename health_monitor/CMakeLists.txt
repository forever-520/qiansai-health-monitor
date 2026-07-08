# health_monitor/CMakeLists.txt
# 生命体征监测系统 — 顶层构建
#
# 交叉编译 (RK3588):
#   cmake -B build -DCMAKE_TOOLCHAIN_FILE=cmake/rk3588-toolchain.cmake
#   cmake --build build
#
# 本地编译验证:
#   cmake -B build
#   cmake --build build

cmake_minimum_required(VERSION 3.10)
project(health_monitor C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

# 子模块
add_subdirectory(src/radar)
add_subdirectory(src/ai)
add_subdirectory(src/webserver)

# src/ui 为前端静态页面，由 webserver 模块直接提供文件服务
# 编译时通过 webserver_start() 传入 ui_root 路径

# 可执行目标（集成所有模块）
add_executable(health_monitor
    src/main.c
)

target_link_libraries(health_monitor
    radar
    ai
    webserver
    pthread
    m
)
