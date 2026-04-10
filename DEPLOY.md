# 三省六部 · 部署指南

## 系统架构

```text
┌─────────────────────────────────────────────────────────────┐
│                      用户浏览器                             │
└────────────────┬──────────────────────────────────────────┘
                 │ http://localhost:7891
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ Dashboard Server (7891) ──代理 POST──→ FastAPI (8000)     │
│ └─ 静态文件服务 (dist/)                   └─ PostgreSQL    │
│ └─ GET 读 JSON 文件                      └─ Redis         │
└─────────────────────────────────────────────────────────────┘
                                       │
             ┌─────────────────────────┼─────────────────────────┐
             ▼                         ▼                         ▼
   ┌──────────────────┐     ┌──────────────────┐      ┌──────────────────┐
   │ outbox_relay     │     │ orchestrator     │      │ dispatcher       │
   │ DB → Redis       │     │ Redis 事件消费   │      │ OpenClaw 派发     │
   └──────────────────┘     └──────────────────┘      └──────────────────┘
                                                               │
                                                               ▼
                                                ┌──────────────────────────┐
                                                │ OpenClaw Runtime / Agents│
                                                └──────────────────────────┘
```

## 前置要求

- Python 3.12+
- PostgreSQL 16+（或 Docker）
- Redis 7+
- OpenClaw CLI（`npm install -g @anthropic-ai/openclaw`）
- Nginx（可选，生产环境推荐）

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://edict:edict_dev_2024@localhost:5432/edict` | PostgreSQL 连接串 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接串 |
| `PORT` | `8000` | FastAPI 监听端口 |
| `EDICT_DASHBOARD_HOST` | `127.0.0.1` | Dashboard 监听地址 |
| `EDICT_DASHBOARD_PORT` | `7891` | Dashboard 端口 |
| `EDICT_BACKEND_PORT` | `8000` | FastAPI Backend 端口 |
| `EDICT_BACKEND_URL` | `http://127.0.0.1:8000` | Dashboard / loop 访问 Backend 地址 |
| `OPENCLAW_PROJECT_DIR` | `/opt/edict` | OpenClaw 工作区目录 |
| `EDICT_LOOP_LOG` | `$REPO_DIR/logs/loop.log` | 数据刷新循环日志 |
| `EDICT_LOOP_PIDFILE` | `$REPO_DIR/.pids/loop.pid` | 数据刷新循环 PID 文件 |

## 方式一：直接脚本（非 systemd）

适用于开发、测试、单机部署。

```bash
# 安装依赖
cd /opt/edict/edict/backend
pip install -r requirements.txt

# 初始化数据库
alembic upgrade head

# 前台主管理模式（推荐）
cd /opt/edict
./edict.sh run

# 或后台启动
./edict.sh start

# 查看状态
./edict.sh status

# 查看日志
./edict.sh logs
./edict.sh logs backend
./edict.sh logs outbox
./edict.sh logs all

# 停止
./edict.sh stop
```

### 启动顺序

1. Backend（uvicorn）
2. outbox_relay
3. orchestrator_worker
4. dispatch_worker
5. dashboard/server.py
6. run_loop.sh（若已安装 OpenClaw CLI）

### `edict.sh` 管理的进程

| PID 文件 | 进程 | 端口 | 说明 |
|----------|------|------|------|
| `.pids/backend.pid` | uvicorn (FastAPI) | 8000 | API 主入口 |
| `.pids/outbox_relay.pid` | outbox_relay | — | DB outbox → Redis Streams |
| `.pids/orchestrator.pid` | orchestrator_worker | — | 事件编排器 |
| `.pids/dispatcher.pid` | dispatch_worker | — | Agent 派发器 |
| `.pids/server.pid` | dashboard/server.py | 7891 | API 代理 + 静态文件 |
| `.pids/loop.pid` | run_loop.sh | — | 数据同步循环 |

### 前台主管理说明

`./edict.sh run` 会以前台方式拉起整套运行栈。任一关键子进程退出时，主管理脚本会清理其他子进程并以非 0 退出，适合被 systemd 直接监督。

## 方式二：systemd（推荐生产环境）

### 首次部署

```bash
# 1. 复制 service 文件
sudo cp /opt/edict/edict.service /etc/systemd/system/

# 2. 重载 systemd
sudo systemctl daemon-reload

# 3. 启用并启动
sudo systemctl enable --now edict

# 4. 查看状态
sudo systemctl status edict

# 5. 查看日志
journalctl -u edict -f
```

### 日常操作

```bash
sudo systemctl start edict
sudo systemctl stop edict
sudo systemctl restart edict
sudo systemctl status edict
journalctl -u edict -f
```

### 说明

- `edict.service` 使用 `Type=simple`
- systemd 直接监督 `./edict.sh run`
- 不再依赖单个 pidfile 判断整套服务是否存活
- `KillMode=control-group` 会在停止时回收整组子进程

### 前置服务

确保 PostgreSQL 和 Redis 已启动：

```bash
sudo systemctl enable --now postgresql
sudo systemctl enable --now redis
```

或者用 Docker 启动基础设施：

```bash
docker run -d --name postgres \
  -e POSTGRES_DB=edict \
  -e POSTGRES_USER=edict \
  -e POSTGRES_PASSWORD=edict_dev_2024 \
  -p 5432:5432 \
  postgres:16-alpine

docker run -d --name redis \
  -p 6379:6379 \
  redis:7-alpine
```

## 方式三：Docker Compose

当前完整部署文件：`edict/docker-compose.yml`

### 启动

```bash
cd /opt/edict/edict
docker compose up -d --build

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f
docker compose logs -f backend
docker compose logs -f outbox_relay
docker compose logs -f dispatcher
```

### 停止

```bash
docker compose down
```

### 服务列表

| 服务 | 端口 | 说明 |
|------|------|------|
| `postgres` | 5432 | PostgreSQL 数据库 |
| `redis` | 6379 | Redis 事件总线 |
| `backend` | 8000 | FastAPI API |
| `outbox_relay` | — | 数据库 outbox 发布器 |
| `orchestrator` | — | 事件编排 Worker |
| `dispatcher` | — | Agent 派发 Worker |
| `dashboard` | 7891 | API 代理 + 静态文件 |
| `frontend` | 3000 | React 前端 |

### Compose 运行说明

- `backend` 启动时先执行 Alembic 迁移，再以前台 uvicorn 运行
- `dashboard` 使用仓库根目录 `Dockerfile` 构建，不再依赖运行时临时 `pip install`
- `outbox_relay` 已纳入默认运行栈，保证 DB → outbox → Redis 事件链路完整

## 数据库初始化

### 直接部署

```bash
cd /opt/edict/edict/backend
alembic upgrade head
```

### Docker Compose

Alembic 迁移在 `backend` 服务的 `command` 中自动执行。

## OpenClaw 集成

### 安装 OpenClaw CLI

```bash
npm install -g @anthropic-ai/openclaw
openclaw --version
```

### 验证 Gateway 可用

```bash
openclaw gateway status
# 或
curl http://localhost:18789/healthz
```

### 当 OpenClaw 不可用时

- Backend / Dashboard 仍可启动
- `dispatch_worker` 无法正常执行 agent 派发
- `run_loop.sh` 会在没有 `openclaw` 命令时跳过启动

## 访问地址

| 服务 | 地址 | 说明 |
|------|------|------|
| Dashboard | http://localhost:7891 | 旧前端（推荐） |
| FastAPI | http://localhost:8000 | 新 API 直连 |
| Swagger 文档 | http://localhost:8000/docs | API 文档 |
| 前端（Docker） | http://localhost:3000 | 新前端 |

## 数据目录

所有 JSON 数据文件、日志和 PID 文件都在仓库根目录下：

```text
/opt/edict/
├── data/
│   ├── live_status.json
│   ├── agent_config.json
│   ├── model_change_log.json
│   ├── pending_model_changes.json
│   ├── tasks_source.json
│   ├── tasks.json
│   ├── officials_stats.json
│   └── officials.json
├── logs/
│   ├── backend.log
│   ├── outbox_relay.log
│   ├── orchestrator.log
│   ├── dispatcher.log
│   ├── server.log
│   └── loop.log
└── .pids/
    ├── backend.pid
    ├── outbox_relay.pid
    ├── orchestrator.pid
    ├── dispatcher.pid
    ├── server.pid
    └── loop.pid
```

## 常见问题

### 1. Backend 启动后立即退出

检查 PostgreSQL 和 Redis 是否可用：

```bash
curl http://localhost:8000/health
psql -U edict -d edict -c "SELECT 1"
redis-cli ping
```

### 2. Dashboard 代理返回 `Backend error`

Backend 未启动或 `EDICT_BACKEND_URL` 配置错误：

```bash
curl http://localhost:8000/health
```

### 3. Workers 报错 `Redis connection refused`

Redis 未启动：

```bash
sudo systemctl start redis
# 或
docker compose up -d redis
```

### 4. OpenClaw 指令无法执行

```bash
which openclaw
openclaw --version
openclaw agents list
```

### 5. systemd 启动后马上退出

优先看主管理脚本日志和 systemd 日志：

```bash
journalctl -u edict -f
./edict.sh status
./edict.sh logs all
```

### 6. Docker Compose dashboard 启动失败

重新构建镜像：

```bash
docker compose build dashboard
docker compose up -d dashboard
```

## 卸载

```bash
sudo systemctl stop edict
sudo systemctl disable edict
sudo rm /etc/systemd/system/edict.service
sudo systemctl daemon-reload

# 如需完全清除运行数据
sudo rm -rf /opt/edict/data/*
```
