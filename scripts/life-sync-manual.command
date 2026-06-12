#!/bin/bash
# =============================================================================
# Phase 18E — life-sync-manual.command
# macOS 手动同步入口
#
# 双击此文件打开 Terminal.app，自动执行：
#   1. preflight 安全检查（Calendar 权限/依赖）
#   2. 如 ready → 执行 sync-pending（最多 10 条）
#   3. 输出日志到 logs/manual-sync.log
#
# Calendar TCC 权限由 LifeSyncCalendarHelper.app 持有，无需授予 .venv Python。
# 此入口不会启用 LaunchAgent 或后台同步。
# =============================================================================

set -euo pipefail

# ── 确定项目根目录 ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/local_api/logs"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/manual-sync.log"

# ── 日志函数（同时输出到终端和日志文件）───────────────────────────────────
log() {
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    local line="[$timestamp] $1"
    echo "$line"
    echo "$line" >> "$LOG_FILE"
}

# ── 安全检查 ─────────────────────────────────────────────────────────────────

# 1. 项目目录存在
if [ ! -d "$PROJECT_DIR" ]; then
    echo "❌ 项目目录不存在: $PROJECT_DIR"
    exit 1
fi
log "✅ 项目目录: $PROJECT_DIR"

# 2. .venv Python 存在
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    log "❌ .venv Python 不存在或不可执行。请先运行: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    echo "按回车键关闭此窗口..."
    read -r
    exit 1
fi
log "✅ .venv Python 可用: $PYTHON_BIN"

# 3. preflight 检查
log ""
SEP=$(printf '=%.0s' {1..70})
log "$SEP"
log "🔍 运行 preflight 安全检查..."
log "$SEP"
log ""

PREFLIGHT_OUTPUT=$(cd "$PROJECT_DIR" && "$PYTHON_BIN" -m local_api.preflight 2>&1 || true)

# 从 JSON 输出提取 readiness
READINESS=$(echo "$PREFLIGHT_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['readiness'])" 2>/dev/null || echo "parse_error")

if [ "$READINESS" = "ready" ]; then
    log "✅ preflight: ready — 继续执行同步"
elif [ "$READINESS" = "not_ready" ]; then
    log "❌ preflight: not_ready — 安全阻塞"
    echo "$PREFLIGHT_OUTPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print()
print('  ⚠️  以下检查未通过：')
for c in d['checks']:
    if c['status'] != 'pass' and c['status'] != 'skip':
        print(f'    ❌ {c[\"name\"]}: {c[\"detail\"]}')
        print(f'       建议: {c[\"recommendation\"]}')
" 2>/dev/null || echo "$PREFLIGHT_OUTPUT"
    log ""
    log "请解决上述问题后再试。"
    log "常见原因：LifeSyncCalendarHelper.app 未获得 Calendar 权限（去 系统设置 → 隐私 → 日历 添加 Helper app）"
    echo ""
    echo "按回车键关闭此窗口..."
    read -r
    exit 1
else
    log "⚠️  无法解析 preflight 输出。完整输出如下："
    echo "$PREFLIGHT_OUTPUT"
    log "安全阻塞 — 请检查环境后重试。"
    echo ""
    echo "按回车键关闭此窗口..."
    read -r
    exit 1
fi

# ── 执行同步 ─────────────────────────────────────────────────────────────────
log ""
log "$SEP"
log "🚀 执行 sync-pending (limit=10)..."
log "$SEP"
log ""

cd "$PROJECT_DIR"
"$PYTHON_BIN" -m local_api.scripts.sync_trigger sync-pending --limit 10 2>&1 | tee -a "$LOG_FILE"
SYNC_EXIT=${PIPESTATUS[0]}

log ""
if [ "$SYNC_EXIT" -eq 0 ]; then
    log "✅ 同步完成"
else
    log "⚠️ 同步返回退出码 $SYNC_EXIT（部分记录可能已处理）"
fi

# ── 完成 ─────────────────────────────────────────────────────────────────────
log ""
log "$SEP"
log "📋 日志已保存: $LOG_FILE"
log "$SEP"
echo ""
echo "按回车键关闭此窗口..."
read -r
