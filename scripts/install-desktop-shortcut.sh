#!/bin/bash
set -euo pipefail

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

if [ -f "$HOME/Desktop/创建日程任务.command" ]; then
    echo "💡 提示: 发现旧版本快捷方式，建议您手动删除以使用新的交互式入口。"
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

echo "✅ 安装成功！现在可以直接在桌面双击 '创建日程任务-交互式.command' 使用了。"
echo "=========================================="