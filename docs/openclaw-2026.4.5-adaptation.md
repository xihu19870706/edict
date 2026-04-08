# OpenClaw 2026.4.5 适配说明（Edict）

## 目标

在**不改变三省六部业务架构与产品交互**的前提下，使 Edict 的运行边界适配当前 OpenClaw 2026.4.5。

## 本次适配点

### 1. `scripts/runtime_adapter.py`
- 增加 OpenClaw 配置文件发现逻辑：
  - `OPENCLAW_CONFIG_PATH`
  - `~/.openclaw/openclaw.json`
  - `~/.openclaw/config.json`
- 增加运行时能力探测：
  - `openclaw --version`
  - `openclaw gateway status`
  - `openclaw skills check`
  - `openclaw agent --help`
- 将 Edict 的 agent 派发统一收敛到适配层。
- 改为使用当前 OpenClaw 2026.4.5 可工作的 CLI 形态：
  - `openclaw agent --agent <id> --message <text> --timeout <sec> --json`
  - `deliver=False` → `--local`
  - `deliver=True` → `--deliver`

### 2. `edict/backend/app/workers/dispatch_worker.py`
- 修复重构残留变量错误。
- 继续通过 `dispatch_agent()` 做派发，不改变 worker / Redis Streams / 任务状态机结构。

### 3. `scripts/sync_agent_config.py`
- 不再只硬编码依赖 `~/.openclaw/openclaw.json`
- 输出 `source` 字段，记录实际读取的 OpenClaw 配置来源
- 缺失配置时生成安全的空配置输出，便于排查

### 4. `install.sh`
- 对当前 OpenClaw 配置路径更宽容
- 减少对旧命令面和单一路径假设的耦合

## 已验证内容

### 真实命令面验证
当前机器上的 OpenClaw 2026.4.5 支持：

```bash
openclaw agent --agent taizi --message "..." --timeout 60 --json --local
openclaw agent --agent taizi --message "..." --timeout 60 --json --deliver
```

### 已通过的运行验证
- `dispatch_agent(..., deliver=False)` ✅
- `dispatch_agent(..., deliver=True)` ✅
- `dispatch_worker` 启动 ✅
- Redis Streams 发布 `task.dispatch` ✅
- `dispatch_worker` 消费任务并调用 OpenClaw ✅
- Redis consumer group pending=0，说明 ACK 完成 ✅

## 仍需注意

1. 当前 Edict worker 以模块方式启动更稳：

```bash
cd /home/edict/edict/edict/backend
PYTHONPATH=/home/edict/edict python3 -m app.workers.dispatch_worker
```

2. `openclaw agent` 是当前验证通过的兼容入口；若未来 OpenClaw 再改 CLI，只需优先调整 `scripts/runtime_adapter.py`。

3. 本次适配只处理“运行边界”，没有改变：
- 三省六部任务流转
- Redis Streams 事件架构
- 前后端产品结构
- Agent 角色设计

## 建议后续

- 将这批适配回归测试纳入 CI
- 若后续要适配更多 OpenClaw 版本，继续保持“业务层不碰、只改 runtime_adapter”原则
