#!/usr/bin/env bash
# archive_eval_report.sh — 归档当前 eval 报告并创建模板
#
# 用法:
#   bash scripts/archive_eval_report.sh
#
# 行为:
#   1. 检查 docs/eval-report.log 是否存在且非空模板
#   2. 若存在，重命名为 docs/eval-report-YYYYmmdd_HHMMSS.log
#   3. 创建新的空白 docs/eval-report.log 模板

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCS_DIR="$PROJECT_ROOT/docs"
REPORT_FILE="$DOCS_DIR/eval-report.log"

mkdir -p "$DOCS_DIR"

if [ ! -s "$REPORT_FILE" ]; then
    echo "[archive_eval_report] eval-report.log 不存在或为空，跳过归档。"
    if [ ! -f "$REPORT_FILE" ]; then
        cat > "$REPORT_FILE" << 'TEMPLATE'
Agent EVAL Report
================

Date:
Branch:
Seed vault:
Test videos:
Valid pairs:

Results:
  dim A (terminology):  —
  dim B (wikilinks):    —
  dim C (suggestions):  —
  dim D (overall):      —

Self-doubt:

Conclusion: —

Root cause:
  —
TEMPLATE
    fi
    exit 0
fi

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ARCHIVE_NAME="eval-report-${TIMESTAMP}.log"
ARCHIVE_PATH="$DOCS_DIR/$ARCHIVE_NAME"

mv "$REPORT_FILE" "$ARCHIVE_PATH"
echo "[archive_eval_report] 已归档 → $ARCHIVE_NAME"

cat > "$REPORT_FILE" << 'TEMPLATE'
Agent EVAL Report
================

Date:
Branch:
Seed vault:
Test videos:
Valid pairs:

Results:
  dim A (terminology):  —
  dim B (wikilinks):    —
  dim C (suggestions):  —
  dim D (overall):      —

Self-doubt:

Conclusion: —

Root cause:
  —
TEMPLATE

echo "[archive_eval_report] 空白模板已初始化 → eval-report.log"
