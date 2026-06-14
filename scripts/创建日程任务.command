#!/bin/bash
set -uo pipefail

# 日常创建日程任务快捷入口
# 本脚本为主创建命令的薄封装，方便直接双击执行。

# 定位到脚本同级目录并调用真实处理脚本
cd "$(dirname "$0")"

# 传递所有参数给主命令（双击时无参数，将直接触发交互模式）
exec ./life-sync-create-task.command "$@"
