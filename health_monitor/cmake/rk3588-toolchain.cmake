# cmake/rk3588-toolchain.cmake
# RK3588 (aarch64) 交叉编译工具链
#
# 使用方法:
#   cmake -B build -DCMAKE_TOOLCHAIN_FILE=cmake/rk3588-toolchain.cmake
#
# 需要安装 aarch64-linux-gnu-gcc:
#   sudo apt install gcc-aarch64-linux-gnu  (Debian/Ubuntu)
# 或在 RK3588 板端本地编译无需此文件。

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
