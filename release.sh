#!/usr/bin/env bash
# liquid-loop 发布脚本
# 用法: ./release.sh [patch|minor|major]  # 默认 patch
# 前置: cp .release_config.example .release_config && 编辑填入真实值

set -euo pipefail

# 读取配置
CONFIG=".release_config"
if [[ ! -f "$CONFIG" ]]; then
    echo "❌ 缺少 $CONFIG，请先复制 .release_config.example 并填写"
    exit 1
fi
source "$CONFIG"

# 版本号递增
BUMP="${1:-patch}"
CURRENT_VERSION=$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"
case "$BUMP" in
    major) MAJOR=$((MAJOR+1)); MINOR=0; PATCH=0 ;;
    minor) MINOR=$((MINOR+1)); PATCH=0 ;;
    patch) PATCH=$((PATCH+1)) ;;
    *) echo "未知版本类型: $BUMP"; exit 1 ;;
esac
NEW_VERSION="$MAJOR.$MINOR.$PATCH"

echo "🚀 发布 liquid-loop v$NEW_VERSION (当前 v$CURRENT_VERSION)"

# 1. 更新版本号
sed -i.bak "s/^version = \".*\"/version = \"$NEW_VERSION\"/" pyproject.toml
rm -f pyproject.toml.bak

# 2. 构建
echo "📦 构建包..."
python3 -m pip install --quiet --upgrade build
python3 -m build

# 3. Git 提交与标签
echo "🏷️  创建 Git 标签..."
git add pyproject.toml
git commit -m "chore: release v$NEW_VERSION"
git tag -a "v$NEW_VERSION" -m "Release v$NEW_VERSION"

# 4. 推送到 GitHub
echo "📤 推送到 GitHub..."
git push origin main --tags

# 5. 发布到 PyPI
echo "📤 发布到 PyPI..."
python3 -m pip install --quiet --upgrade twine
python3 -m twine upload --username __token__ --password "$PYPI_TOKEN" dist/*

# 6. 清理
rm -rf dist build *.egg-info

echo ""
echo "✅ 发布完成！"
echo "   版本: v$NEW_VERSION"
echo "   PyPI: https://pypi.org/project/liquid-loop/$NEW_VERSION/"
echo "   GitHub: https://github.com/fishbook0001/liquid-loop/releases/tag/v$NEW_VERSION"
echo ""
echo "📦 安装命令: pip install liquid-loop==$NEW_VERSION"