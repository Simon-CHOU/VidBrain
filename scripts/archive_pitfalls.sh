#!/usr/bin/env bash
# archive_pitfalls.sh — 归档当前 pitfall 日志并创建新的空白日志
#
# 用法:
#   bash scripts/archive_pitfalls.sh
#
# 行为:
#   1. 检查 docs/pitfall-problems.log 是否存在
#   2. 若存在，重命名为 docs/pitfall-problems-YYYYmmdd_HHMMSS.log
#   3. 创建新的 docs/pitfall-problems.log，包含标准表格头
#
# 设计意图:
#   每个 Session 独立一个日志文件，避免单文件无限膨胀；
#   新 Session 始终从空白 pitfall-problems.log 开始记录，
#   历史问题按时间戳归档，便于按日期查找。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCS_DIR="$PROJECT_ROOT/docs"
PITFALL_FILE="$DOCS_DIR/pitfall-problems.log"

# 确保 docs 目录存在
mkdir -p "$DOCS_DIR"

# 如果 pitfall-problems.log 不存在或为空，无需归档
if [ ! -s "$PITFALL_FILE" ]; then
    echo "[archive_pitfalls] pitfall-problems.log 不存在或为空，跳过归档。"
    # 仍然确保空白模板存在
    if [ ! -f "$PITFALL_FILE" ]; then
        touch "$PITFALL_FILE"
    fi
    exit 0
fi

# 排除纯模板文件（只有表头和分隔行的空文件）
NON_HEADER_LINES=$(grep -cvE '^\s*(\|#|$\|)' "$PITFALL_FILE" 2>/dev/null || echo "0")
if [ "$NON_HEADER_LINES" -eq 0 ]; then
    echo "[archive_pitfalls] pitfall-problems.log 仅含表头（无实际条目），无需归档。"
    exit 0
fi

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ARCHIVE_NAME="pitfall-problems-${TIMESTAMP}.log"
ARCHIVE_PATH="$DOCS_DIR/$ARCHIVE_NAME"

# 归档
mv "$PITFALL_FILE" "$ARCHIVE_PATH"
echo "[archive_pitfalls] 已归档 → $ARCHIVE_NAME"

# 创建新的空白日志（含表头）
cat > "$PITFALL_FILE" << 'TABLE_HEADER'
# Pitfall Problems Log — VidBrain

> 记录 Session 中具有工程挑战价值的问题、阻塞性卡点、架构决策变更。
> 按时间倒序排列，最新问题在顶部。

| # | 时间 | 问题描述 (核心挑战) | 复现/触发路径 | 解决方案 (及核心工程逻辑) | 是否已解决 | 面试素材价值 (技术深度点) |
|---|------|-------------------|-------------|----------------------|----------|------------------------|
TABLE_HEADER

echo "[archive_pitfalls] 空白日志已初始化 → pitfall-problems.log"
