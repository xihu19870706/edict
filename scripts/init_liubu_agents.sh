#!/bin/bash
# 六部 Agent 批量初始化脚本
# 用途：直接写入 IDENTITY/USER 文件并删除 BOOTSTRAP.md，使 agent 跳过初始化对话直接可接受任务
# 用法: ./init_liubu_agents.sh

set -euo pipefail

AGENTS=("gongbu" "bingbu" "hubu" "libu" "xingbu")

declare -A AGENT_NAMES=(
  [gongbu]="工部尚书·公输班"
  [bingbu]="兵部尚书·孙武"
  [hubu]="户部尚书·管仲"
  [libu]="礼部尚书·周公"
  [xingbu]="刑部尚书·商鞅"
)

declare -A AGENT_EMOJI=(
  [gongbu]="🔧"
  [bingbu]="⚔️"
  [hubu]="📊"
  [libu]="📜"
  [xingbu]="⚖️"
)

declare -A AGENT_VIBE=(
  [gongbu]="扎实严谨，工程导向，追求稳如磐石"
  [bingbu]="锐意进取，开发导向，追求出新突破"
  [hubu]="务实细致，数据导向，追求精准洞察"
  [libu]="温文尔雅，沟通导向，追求清晰表达"
  [xingbu]="公正严明，审查导向，追求滴水不漏"
)

BASE="/home/edict/.openclaw"

for AG in "${AGENTS[@]}"; do
  WS="$BASE/workspace-$AG"
  echo "=== 初始化 $AG ==="

  # 创建工作区目录
  mkdir -p "$WS"

  # 写 IDENTITY.md
  cat > "$WS/IDENTITY.md" << EOF
# IDENTITY.md - Who Am I?

- **Name:**
  ${AGENT_NAMES[$AG]}

- **Creature:**
  AI Agent（${AG}尚书，省六部之一）

- **Vibe:**
  ${AGENT_VIBE[$AG]}

- **Emoji:**
  ${AGENT_EMOJI[$AG]}

- **Avatar:**
  （待配置）
EOF

  # 写 USER.md
  cat > "$WS/USER.md" << EOF
# USER.md - About Your Human

- **Name:** 尚书省
- **What to call them:** 尚书省大人
- **Timezone:** Asia/Shanghai

## Context

本 agent 由尚书省统一调度，接收尚书省派发的任务令后执行。
执行完毕后统一回报尚书省汇总。
不自行跨部门协作，所有协调通过尚书省进行。
EOF

  # 删除 BOOTSTRAP.md（表示已完成初始化）
  if [[ -f "$WS/BOOTSTRAP.md" ]]; then
    rm -f "$WS/BOOTSTRAP.md"
    echo "  ✓ 删除 BOOTSTRAP.md"
  else
    echo "  ✓ BOOTSTRAP.md 不存在（已初始化或无文件）"
  fi

  # 确保必要目录存在（跳过 data，因其为 symlink）
  mkdir -p "$WS/scripts" "$WS/skills"
  mkdir -p "$BASE/agents/$AG/sessions" "$BASE/agents/$AG/agent"

  echo "  ✓ $AG 初始化完成"
  echo ""
done

echo "=== 全部完成 ==="
echo "注意：xinbu（xingbu）需要确保 /home/edict/.openclaw/agents/xingbu/agent 目录存在"
ls -la "$BASE/agents/xingbu/" 2>/dev/null || echo "xingbu agent 目录缺失，需手动创建"
