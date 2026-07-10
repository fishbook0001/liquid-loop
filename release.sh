#!/usr/bin/env bash
# liquid-loop 一键发布脚本
# 用法: ./release.sh [版本号] [提交信息]
# 例: ./release.sh v0.1.0 "Initial release"

set -euo pipefail

VERSION="${1:-v0.1.0}"
MSG="${2:-Release $VERSION}"

echo "🚀 发布 liquid-loop $VERSION"
echo "=================================="

# 1. 检查必要工具
for cmd in git python3 twine; do
    if ! command -v $cmd &>/dev/null; then
        echo "❌ 缺少 $cmd，请先安装"
        exit 1
    fi
done

# 2. 读取配置
if [[ ! -f .release_config ]]; then
    echo "❌ 缺少 .release_config，请先配置："
    cat << 'EOF' > .release_config.example
GITEE_USER=your_gitee_username
GITEE_REPO=liquid-loop
PYPI_TOKEN=pypi-xxxxxxxxxxxxxxxx
EOF
    echo "请复制 .release_config.example 为 .release_config 并填入真实值"
    exit 1
fi

source .release_config

# 3. 同步版本号到 pyproject.toml
VER_NO_V="${VERSION#v}"
sed -i.bak "s/^version = \".*\"/version = \"$VER_NO_V\"/" pyproject.toml
rm -f pyproject.toml.bak
echo "📝 版本号已更新: $VER_NO_V"

# 4. Git 提交与推送
echo "📦 Git 提交..."
git add -A
git commit -m "$MSG" || true  # 允许无变化
git tag -f "$VERSION"
git push origin main --tags

echo "🌐 推送到 Gitee..."
git push "https://gitee.com/$GITEE_USER/$GITEE_REPO.git" main --tags -f

# 5. 构建包
echo "🔨 构建分发包..."
rm -rf dist build *.egg-info
python3 -m build

# 6. 上传 PyPI
echo "📤 上传到 PyPI..."
twine upload -u __token__ -p "$PYPI_TOKEN" dist/*

echo ""
echo "✅ 发布完成！"
echo "   PyPI: https://pypi.org/project/liquid-loop/$VER_NO_V/"
echo "   Gitee: https://gitee.com/$GITEE_USER/$GITEE_REPO/releases/tag/$VERSION"