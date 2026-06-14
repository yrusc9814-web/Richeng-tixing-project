#!/bin/bash
set -euo pipefail

# 获取当前工程绝对路径
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$HOME/Desktop/创建日程任务-交互式.command"

echo "=========================================="
echo "    安装 LifeSync 日常任务桌面快捷入口    "
echo "=========================================="

if [ -f "$TARGET" ]; then
    echo "⚠️  发现桌面已存在同名文件: $TARGET"
    echo "正在备份原文件到 ${TARGET}.bak..."
    mv "$TARGET" "${TARGET}.bak"
fi

# 如果之前安装过非交互式的版本，可以选择保留或提醒，这里简单提醒
OLD_TARGET="$HOME/Desktop/创建日程任务.command"
if [ -f "$OLD_TARGET" ]; then
    echo "💡 提示: 发现旧版本快捷方式 $OLD_TARGET，建议您手动删除它以使用新的交互式入口。"
fi

echo "正在生成桌面快捷方式..."

cat << EOF > "$TARGET"
#!/bin/bash
set -uo pipefail

# LifeSync 桌面快捷入口
# 该脚本将自动切换到工程根目录并执行主逻辑

cd "$PROJECT_ROOT"
exec ./scripts/创建日程任务-交互式.command "\$@"
EOF

chmod +x "$TARGET"

echo "✅ 安装成功！现在可以直接在桌面双击 '创建日程任务.command' 使用了。"
echo "=========================================="
