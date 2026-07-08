#!/bin/bash
# health_monitor 第三方依赖下载脚本
# 用法: bash scripts/setup-third-party.sh
#
# 在当前网络环境下运行，下载 CivetWeb 嵌入到 third_party/

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
THIRD_PARTY="$SCRIPT_DIR/../third_party"

echo "==> 下载 CivetWeb 1.17..."

CIVETWEB_DIR="$THIRD_PARTY/civetweb"
mkdir -p "$CIVETWEB_DIR/include"
mkdir -p "$CIVETWEB_DIR/src"

# 下载头文件
curl -sL "https://raw.githubusercontent.com/civetweb/civetweb/master/include/civetweb.h" \
    -o "$CIVETWEB_DIR/include/civetweb.h"
echo "    civetweb.h 下载完成 ($(wc -c < "$CIVETWEB_DIR/include/civetweb.h") bytes)"

# 下载实现文件
curl -sL "https://raw.githubusercontent.com/civetweb/civetweb/master/src/civetweb.c" \
    -o "$CIVETWEB_DIR/civetweb.c"
echo "    civetweb.c 下载完成 ($(wc -c < "$CIVETWEB_DIR/civetweb.c") bytes)"

# 验证
if [ ! -s "$CIVETWEB_DIR/include/civetweb.h" ] || [ ! -s "$CIVETWEB_DIR/civetweb.c" ]; then
    echo "错误: 文件下载不完整"
    exit 1
fi

echo ""
echo "==> 所有第三方依赖已下载到 $THIRD_PARTY"
echo "    现在可以执行 cmake -B build && cmake --build build"
