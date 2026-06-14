#!/bin/bash
set -uo pipefail

# 获取当前工程绝对路径
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# 此脚本为交互式任务创建的薄封装。
# 它通过构造参数调用已有的 CLI 入口。

echo "=========================================="
echo "       LifeSync 交互式创建日程任务        "
echo "=========================================="
echo

# 标题输入
while true; do
    if ! read -p "任务标题 (必填, 输入 q 取消): " TITLE; then exit 1; fi
    if [ "$TITLE" = "q" ] || [ "$TITLE" = "Q" ]; then
        echo "已取消任务创建。"
        read -n 1 -s -r -p "按任意键退出..."
        echo
        exit 0
    fi
    
    if [ -n "$(echo "$TITLE" | tr -d '[:space:]')" ]; then
        break
    else
        echo "❌ 错误: 标题不能为空，请重新输入"
    fi
done

# 预计算默认时间
NOW=$(date -u +'%Y-%m-%dT%H:00:00Z')
LATER=$(date -u -v+1H +'%Y-%m-%dT%H:00:00Z' 2>/dev/null || date -u -d '+1 hour' +'%Y-%m-%dT%H:00:00Z')

# 开始时间输入
while true; do
    if ! read -p "开始时间 (ISO格式, 默认 $NOW, 输入 q 取消): " START; then exit 1; fi
    if [ "$START" = "q" ] || [ "$START" = "Q" ]; then
        echo "已取消任务创建。"
        read -n 1 -s -r -p "按任意键退出..."
        echo
        exit 0
    fi
    
    START=${START:-$NOW}
    if date -j -f "%Y-%m-%dT%H:%M:%SZ" "$START" >/dev/null 2>&1 || date -d "$START" >/dev/null 2>&1; then
        break
    else
        echo "❌ 错误: 时间格式错误。必须为 ISO 格式，例如: 2026-06-13T10:00:00Z"
    fi
done

# 结束时间输入
while true; do
    if ! read -p "结束时间 (ISO格式, 默认 $LATER, 输入 q 取消): " END; then exit 1; fi
    if [ "$END" = "q" ] || [ "$END" = "Q" ]; then
        echo "已取消任务创建。"
        read -n 1 -s -r -p "按任意键退出..."
        echo
        exit 0
    fi
    
    END=${END:-$LATER}
    if date -j -f "%Y-%m-%dT%H:%M:%SZ" "$END" >/dev/null 2>&1 || date -d "$END" >/dev/null 2>&1; then
        # 简单的时间先后顺序校验（兼容 MacOS / GNU）
        if date -j -f "%Y-%m-%dT%H:%M:%SZ" "$START" "+%s" >/dev/null 2>&1; then
            S_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$START" "+%s" 2>/dev/null)
            E_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$END" "+%s" 2>/dev/null)
        else
            S_EPOCH=$(date -d "$START" "+%s" 2>/dev/null)
            E_EPOCH=$(date -d "$END" "+%s" 2>/dev/null)
        fi
        
        if [ -n "$S_EPOCH" ] && [ -n "$E_EPOCH" ] && [ "$E_EPOCH" -le "$S_EPOCH" ]; then
            echo "❌ 错误: 结束时间必须晚于开始时间"
        else
            break
        fi
    else
        echo "❌ 错误: 时间格式错误。必须为 ISO 格式，例如: 2026-06-13T10:00:00Z"
    fi
done

if ! read -p "是否立即同步到 Apple Calendar? (Y/n, 默认 Y): " DO_SYNC; then exit 1; fi
DO_SYNC=${DO_SYNC:-Y}

echo
echo "------------------------------------------"
echo "正在调用底层引擎创建任务..."

cd "$PROJECT_ROOT"
# 构建参数调用现有的 wrapper 脚本
ARGS=("--title" "$TITLE" "--start" "$START" "--end" "$END")
if [ "$DO_SYNC" != "n" ] && [ "$DO_SYNC" != "N" ]; then
    ARGS+=("--sync")
fi

if ./scripts/创建日程任务.command "${ARGS[@]}"; then
    echo "------------------------------------------"
else
    echo "------------------------------------------"
    echo "❌ 创建或同步过程中遇到错误，请参考上方日志。"
fi

read -n 1 -s -r -p "按任意键退出..."
echo
