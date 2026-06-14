#!/bin/bash
set -uo pipefail

# Phase 47 — Wrapper command script for task creation (Interactive + CLI)

# Ensure we run from the project root
# If this is run via double-click in Finder, $0 is an absolute path.
cd "$(dirname "$0")/.."
PROJECT_ROOT="$PWD"

# Determine Python executable
if [ -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
    PYTHON="${PROJECT_ROOT}/.venv/bin/python"
elif [ -x "${PROJECT_ROOT}/venv/bin/python" ]; then
    PYTHON="${PROJECT_ROOT}/venv/bin/python"
else
    PYTHON="python3"
fi

if [ $# -eq 0 ]; then
    # Interactive mode for double-click
    echo "==================================="
    echo "       LifeSync 任务创建           "
    echo "==================================="
    echo
    
    read -p "任务标题 (必填): " TITLE
    if [ -z "$(echo "$TITLE" | tr -d '[:space:]')" ]; then
        echo "❌ 错误: 标题不能为空"
        read -n 1 -s -r -p "按任意键退出..."
        echo
        exit 1
    fi
    
    NOW=$(date -u +'%Y-%m-%dT%H:00:00Z')
    LATER=$(date -u -v+1H +'%Y-%m-%dT%H:00:00Z' 2>/dev/null || date -u -d '+1 hour' +'%Y-%m-%dT%H:00:00Z')
    
    read -p "开始时间 (默认 $NOW): " START
    START=${START:-$NOW}
    
    read -p "结束时间 (默认 $LATER): " END
    END=${END:-$LATER}
    
    read -p "备注 (可选): " NOTES
    
    read -p "是否立即同步? (Y/n, 默认 Y): " DO_SYNC
    DO_SYNC=${DO_SYNC:-Y}
    
    ARGS=("--title" "$TITLE" "--start" "$START" "--end" "$END")
    if [ -n "$NOTES" ]; then
        ARGS+=("--notes" "$NOTES")
    fi
    if [ "$DO_SYNC" != "n" ] && [ "$DO_SYNC" != "N" ]; then
        ARGS+=("--sync")
    fi
    
    echo
    echo "正在创建任务..."
    echo "-----------------------------------"
    if "$PYTHON" -m local_api.scripts.create_task "${ARGS[@]}"; then
        echo "-----------------------------------"
        echo "✅ 操作成功完成"
    else
        echo "-----------------------------------"
        echo "❌ 操作失败，请查看上方错误信息"
    fi
    
    echo
    read -n 1 -s -r -p "按任意键退出..."
    echo
else
    # CLI mode (pass-through)
    exec "$PYTHON" -m local_api.scripts.create_task "$@"
fi
