#!/bin/bash

# Dreamina CapCut 账号自动注册 - 快速启动脚本

echo "=================================================="
echo "Dreamina CapCut 账号自动注册工具"
echo "=================================================="
echo ""

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未找到 Python3"
    exit 1
fi

# 检查虚拟环境
if [ ! -d ".venv" ]; then
    echo "⚠️  警告: 未找到虚拟环境，正在创建..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

# 默认参数
TARGET_COUNT=${1:-10}  # 第一个参数为目标数量，默认10
CONCURRENT=${2:-1}     # 第二个参数为并发数，默认1

echo "📋 运行配置:"
echo "  目标成功账号数: $TARGET_COUNT"
echo "  并发数量: $CONCURRENT"
echo "  运行模式: 无头模式"
echo ""

# 运行程序
python3 main.py -t "$TARGET_COUNT" -c "$CONCURRENT" --headless

# 显示退出码
EXIT_CODE=$?
echo ""
echo "=================================================="
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ 程序执行成功"
else
    echo "❌ 程序执行失败，退出码: $EXIT_CODE"
fi
echo "=================================================="

exit $EXIT_CODE
