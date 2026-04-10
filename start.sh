#!/bin/bash
# ══════════════════════════════════════════════════════════════
# 三省六部 · 一键启动脚本（委托给 edict.sh run）
# ══════════════════════════════════════════════════════════════

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

exec bash "$REPO_DIR/edict.sh" run
