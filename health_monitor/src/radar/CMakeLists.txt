# health_monitor/src/radar/CMakeLists.txt
# R60ABD1 雷达驱动库
#
# 用法：
#   mkdir build && cd build
#   cmake .. -DCMAKE_TOOLCHAIN_FILE=..  (或使用本地 GCC 直接编译验证)
#   make

cmake_minimum_required(VERSION 3.10)
project(r60abd1 C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

# 编译静态库
add_library(r60abd1 STATIC
    r60abd1.c
    r60abd1.h
    r60_uart.c
    r60_uart.h
)

target_include_directories(r60abd1 PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}
)

# 编译测试可执行文件（仅在本地构建时启用）
option(BUILD_TESTS "Build test executable" OFF)
if(BUILD_TESTS)
    add_executable(r60abd1_test test_main.c)
    target_link_libraries(r60abd1_test r60abd1)
endif()
