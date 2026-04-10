#!/bin/bash
# ══════════════════════════════════════════════════════════════
# 三省六部 · 统一服务管理脚本
# 用法: ./edict.sh {run|start|stop|status|restart|logs}
# systemd 部署: ./edict.sh run
# ══════════════════════════════════════════════════════════════

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDDIR="$REPO_DIR/.pids"
LOGDIR="$REPO_DIR/logs"

BACKEND_PIDFILE="$PIDDIR/backend.pid"
OUTBOX_PIDFILE="$PIDDIR/outbox_relay.pid"
ORCH_PIDFILE="$PIDDIR/orchestrator.pid"
DISP_PIDFILE="$PIDDIR/dispatcher.pid"
SERVER_PIDFILE="$PIDDIR/server.pid"
LOOP_PIDFILE="$PIDDIR/loop.pid"

BACKEND_LOG="$LOGDIR/backend.log"
OUTBOX_LOG="$LOGDIR/outbox_relay.log"
ORCH_LOG="$LOGDIR/orchestrator.log"
DISP_LOG="$LOGDIR/dispatcher.log"
SERVER_LOG="$LOGDIR/server.log"
LOOP_LOG="$LOGDIR/loop.log"

DASHBOARD_HOST="${EDICT_DASHBOARD_HOST:-127.0.0.1}"
DASHBOARD_PORT="${EDICT_DASHBOARD_PORT:-7891}"
BACKEND_PORT="${EDICT_BACKEND_PORT:-8000}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

SUPERVISOR_MODE=0
SUPERVISOR_SHUTTING_DOWN=0
SUPERVISOR_CLEANED_UP=0
SUPERVISOR_PIDS=()

_ensure_dirs() {
  mkdir -p "$PIDDIR" "$LOGDIR" "$REPO_DIR/data"
  touch "$BACKEND_LOG" "$OUTBOX_LOG" "$ORCH_LOG" "$DISP_LOG" "$SERVER_LOG" "$LOOP_LOG"

  for f in live_status.json agent_config.json model_change_log.json sync_status.json; do
    if [[ ! -f "$REPO_DIR/data/$f" ]]; then
      echo '{}' > "$REPO_DIR/data/$f"
    fi
  done
  if [[ ! -f "$REPO_DIR/data/pending_model_changes.json" ]]; then
    echo '[]' > "$REPO_DIR/data/pending_model_changes.json"
  fi
  if [[ ! -f "$REPO_DIR/data/tasks_source.json" ]]; then
    echo '[]' > "$REPO_DIR/data/tasks_source.json"
  fi
  if [[ ! -f "$REPO_DIR/data/tasks.json" ]]; then
    echo '[]' > "$REPO_DIR/data/tasks.json"
  fi
  if [[ ! -f "$REPO_DIR/data/officials.json" ]]; then
    echo '[]' > "$REPO_DIR/data/officials.json"
  fi
  if [[ ! -f "$REPO_DIR/data/officials_stats.json" ]]; then
    echo '{}' > "$REPO_DIR/data/officials_stats.json"
  fi
}

_prepare_runtime_env() {
  export EDICT_HOME="${EDICT_HOME:-$REPO_DIR}"
  export OPENCLAW_PROJECT_DIR="${OPENCLAW_PROJECT_DIR:-$REPO_DIR}"
  export PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}"
  export EDICT_BACKEND_URL="${EDICT_BACKEND_URL:-http://127.0.0.1:${BACKEND_PORT}}"
  export EDICT_LOOP_LOG="${EDICT_LOOP_LOG:-$LOOP_LOG}"
  export EDICT_LOOP_PIDFILE="${EDICT_LOOP_PIDFILE:-$LOOP_PIDFILE}"
}

_is_running() {
  local pidfile="$1"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid=$(cat "$pidfile" 2>/dev/null)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    rm -f "$pidfile"
  fi
  return 1
}

_get_pid() {
  local pidfile="$1"
  if [[ -f "$pidfile" ]]; then
    cat "$pidfile" 2>/dev/null
  fi
}

_kill_safe() {
  local label="$1"
  local pidfile="$2"
  if _is_running "$pidfile"; then
    local pid
    pid=$(_get_pid "$pidfile")
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
    echo -e "  ✅ ${label} (PID=$pid) 已停止"
  fi
}

_launch_service() {
  local label="$1"
  local pidfile="$2"
  local logfile="$3"
  local workdir="$4"
  shift 4

  (
    cd "$workdir"
    exec "$@" >> "$logfile" 2>&1
  ) &
  local pid=$!
  echo "$pid" > "$pidfile"

  if (( SUPERVISOR_MODE )); then
    SUPERVISOR_PIDS+=("$pid")
  fi

  echo -e "  PID=$pid  日志: ${BLUE}$logfile${NC}"
}

_wait_backend_ready() {
  for _ in $(seq 1 120); do
    if curl -sf "http://127.0.0.1:${BACKEND_PORT}/health" > /dev/null 2>&1; then
      echo -e "  ${GREEN}✅ Backend 已就绪${NC}"
      return 0
    fi
    sleep 0.5
  done

  echo -e "${RED}❌ Backend 启动超时，请检查日志: $BACKEND_LOG${NC}"
  return 1
}

_assert_not_running() {
  local has_running=0
  for entry in \
    "Backend:$BACKEND_PIDFILE" \
    "Outbox Relay:$OUTBOX_PIDFILE" \
    "Orchestrator:$ORCH_PIDFILE" \
    "Dispatcher:$DISP_PIDFILE" \
    "Dashboard:$SERVER_PIDFILE" \
    "数据刷新循环:$LOOP_PIDFILE"; do
    local name="${entry%%:*}"
    local pidfile="${entry#*:}"
    if _is_running "$pidfile"; then
      echo -e "${YELLOW}⚠️  ${name} 已在运行 (PID=$(_get_pid "$pidfile"))${NC}"
      has_running=1
    fi
  done

  if (( has_running )); then
    echo -e "${RED}❌ 检测到已有运行中的服务，请先执行 ./edict.sh stop${NC}"
    exit 1
  fi
}

_start_backend() {
  if _is_running "$BACKEND_PIDFILE"; then
    echo -e "${YELLOW}⚠️  Backend 已在运行 (PID=$(_get_pid "$BACKEND_PIDFILE"))${NC}"
    return 0
  fi

  echo -e "${GREEN}▶ 启动 FastAPI Backend (port ${BACKEND_PORT})...${NC}"
  _launch_service "Backend" "$BACKEND_PIDFILE" "$BACKEND_LOG" "$REPO_DIR/edict/backend" \
    python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT"
  _wait_backend_ready
}

_start_outbox_relay() {
  if _is_running "$OUTBOX_PIDFILE"; then
    echo -e "${YELLOW}⚠️  Outbox Relay 已在运行 (PID=$(_get_pid "$OUTBOX_PIDFILE"))${NC}"
    return 0
  fi

  echo -e "${GREEN}▶ 启动 Outbox Relay...${NC}"
  _launch_service "Outbox Relay" "$OUTBOX_PIDFILE" "$OUTBOX_LOG" "$REPO_DIR/edict/backend" \
    python3 -m app.workers.outbox_relay
  sleep 1
}

_start_orchestrator() {
  if _is_running "$ORCH_PIDFILE"; then
    echo -e "${YELLOW}⚠️  Orchestrator 已在运行 (PID=$(_get_pid "$ORCH_PIDFILE"))${NC}"
    return 0
  fi

  echo -e "${GREEN}▶ 启动 Orchestrator Worker...${NC}"
  _launch_service "Orchestrator" "$ORCH_PIDFILE" "$ORCH_LOG" "$REPO_DIR/edict/backend" \
    python3 -m app.workers.orchestrator_worker
  sleep 1
}

_start_dispatcher() {
  if _is_running "$DISP_PIDFILE"; then
    echo -e "${YELLOW}⚠️  Dispatcher 已在运行 (PID=$(_get_pid "$DISP_PIDFILE"))${NC}"
    return 0
  fi

  echo -e "${GREEN}▶ 启动 Dispatch Worker...${NC}"
  _launch_service "Dispatcher" "$DISP_PIDFILE" "$DISP_LOG" "$REPO_DIR/edict/backend" \
    python3 -m app.workers.dispatch_worker
  sleep 1
}

_start_dashboard() {
  if _is_running "$SERVER_PIDFILE"; then
    echo -e "${YELLOW}⚠️  Dashboard 已在运行 (PID=$(_get_pid "$SERVER_PIDFILE"))${NC}"
    return 0
  fi

  echo -e "${GREEN}▶ 启动 Dashboard Server (port ${DASHBOARD_PORT})...${NC}"
  _launch_service "Dashboard" "$SERVER_PIDFILE" "$SERVER_LOG" "$REPO_DIR" \
    python3 dashboard/server.py --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT"
  sleep 1
}

_start_loop() {
  if _is_running "$LOOP_PIDFILE"; then
    echo -e "${YELLOW}⚠️  数据刷新循环已在运行 (PID=$(_get_pid "$LOOP_PIDFILE"))${NC}"
    return 0
  fi

  if ! command -v openclaw &>/dev/null; then
    echo -e "${YELLOW}⚠️  未检测到 OpenClaw CLI，跳过数据刷新循环${NC}"
    return 0
  fi

  echo -e "${GREEN}▶ 启动数据刷新循环...${NC}"
  _launch_service "数据刷新循环" "$LOOP_PIDFILE" "$LOOP_LOG" "$REPO_DIR" \
    bash "$REPO_DIR/scripts/run_loop.sh"
  sleep 1
}

_start_stack() {
  _start_backend
  _start_outbox_relay
  _start_orchestrator
  _start_dispatcher
  _start_dashboard
  _start_loop
}

_cleanup_stack() {
  if (( SUPERVISOR_CLEANED_UP )); then
    return 0
  fi
  SUPERVISOR_CLEANED_UP=1

  echo -e "${YELLOW}正在关闭服务...${NC}"
  _kill_safe "数据刷新循环" "$LOOP_PIDFILE"
  _kill_safe "Dashboard" "$SERVER_PIDFILE"
  _kill_safe "Dispatcher" "$DISP_PIDFILE"
  _kill_safe "Orchestrator" "$ORCH_PIDFILE"
  _kill_safe "Outbox Relay" "$OUTBOX_PIDFILE"
  _kill_safe "Backend" "$BACKEND_PIDFILE"
}

_handle_supervisor_signal() {
  SUPERVISOR_SHUTTING_DOWN=1
  _cleanup_stack
  exit 0
}

do_run() {
  _ensure_dirs
  _prepare_runtime_env
  _assert_not_running

  if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ 未找到 python3，请先安装 Python 3.9+${NC}"
    exit 1
  fi

  SUPERVISOR_MODE=1
  SUPERVISOR_SHUTTING_DOWN=0
  SUPERVISOR_CLEANED_UP=0
  SUPERVISOR_PIDS=()
  trap _handle_supervisor_signal SIGINT SIGTERM

  echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║  三省六部 · 前台主管理启动中            ║${NC}"
  echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
  echo ""

  _start_stack

  echo ""
  echo -e "${GREEN}✅ 服务已启动${NC}"
  echo -e "   Backend:   ${BLUE}http://127.0.0.1:${BACKEND_PORT}${NC}"
  echo -e "   Dashboard: ${BLUE}http://${DASHBOARD_HOST}:${DASHBOARD_PORT}${NC}"
  echo ""

  wait -n "${SUPERVISOR_PIDS[@]}"
  local status=$?

  if (( SUPERVISOR_SHUTTING_DOWN )); then
    exit 0
  fi

  echo -e "${RED}❌ 检测到子进程退出，正在停止整个运行栈${NC}"
  _cleanup_stack
  exit "${status:-1}"
}

do_start() {
  _ensure_dirs
  _prepare_runtime_env

  if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ 未找到 python3，请先安装 Python 3.9+${NC}"
    exit 1
  fi

  echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║  三省六部 · 服务启动中                  ║${NC}"
  echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
  echo ""

  _start_stack

  echo ""
  echo -e "${GREEN}✅ 服务已启动！${NC}"
  echo -e "   Backend:   ${BLUE}http://127.0.0.1:${BACKEND_PORT}${NC}"
  echo -e "   Dashboard: ${BLUE}http://${DASHBOARD_HOST}:${DASHBOARD_PORT}${NC}"
  echo ""
}

do_stop() {
  _cleanup_stack
  echo -e "${GREEN}✅ 所有服务已关闭${NC}"
}

do_status() {
  echo -e "${BLUE}🏛️  三省六部 · 服务状态${NC}"
  echo ""

  for entry in \
    "Backend:$BACKEND_PIDFILE" \
    "Outbox Relay:$OUTBOX_PIDFILE" \
    "Orchestrator:$ORCH_PIDFILE" \
    "Dispatcher:$DISP_PIDFILE" \
    "Dashboard:$SERVER_PIDFILE" \
    "数据刷新循环:$LOOP_PIDFILE"; do
    local name="${entry%%:*}"
    local pidfile="${entry#*:}"
    if _is_running "$pidfile"; then
      echo -e "  ${GREEN}●${NC} ${name}  PID=$(_get_pid "$pidfile")  ${GREEN}运行中${NC}"
    else
      echo -e "  ${RED}○${NC} ${name}  ${RED}未运行${NC}"
    fi
  done

  echo ""
  if _is_running "$SERVER_PIDFILE"; then
    local health
    health=$(python3 -c "
import urllib.request, json
try:
    r = urllib.request.urlopen('http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/healthz', timeout=3)
    d = json.loads(r.read())
    print('healthy' if d.get('status') == 'ok' else 'unhealthy')
except Exception:
    print('unreachable')
" 2>/dev/null) || health="error"
    case "$health" in
      healthy)   echo -e "  Dashboard 健康检查: ${GREEN}✅ 正常${NC}" ;;
      unhealthy) echo -e "  Dashboard 健康检查: ${YELLOW}⚠️  异常${NC}" ;;
      *)         echo -e "  Dashboard 健康检查: ${RED}❌ 无法连接${NC}" ;;
    esac
  fi

  if _is_running "$BACKEND_PIDFILE"; then
    local backend_health
    backend_health=$(python3 -c "
import urllib.request
try:
    urllib.request.urlopen('http://127.0.0.1:${BACKEND_PORT}/health', timeout=3)
    print('healthy')
except Exception:
    print('unreachable')
" 2>/dev/null) || backend_health="error"
    case "$backend_health" in
      healthy) echo -e "  Backend 健康检查:   ${GREEN}✅ 正常${NC}" ;;
      *)       echo -e "  Backend 健康检查:   ${RED}❌ 无法连接${NC}" ;;
    esac
  fi
}

do_logs() {
  local target="${1:-all}"
  case "$target" in
    backend)       tail -f "$BACKEND_LOG" ;;
    outbox|relay)  tail -f "$OUTBOX_LOG" ;;
    orchestrator)  tail -f "$ORCH_LOG" ;;
    dispatcher)    tail -f "$DISP_LOG" ;;
    server)        tail -f "$SERVER_LOG" ;;
    loop)          tail -f "$LOOP_LOG" ;;
    all)           tail -f "$BACKEND_LOG" "$OUTBOX_LOG" "$ORCH_LOG" "$DISP_LOG" "$SERVER_LOG" "$LOOP_LOG" ;;
    *)
      echo "用法: $0 logs [backend|outbox|relay|orchestrator|dispatcher|server|loop|all]"
      exit 1
      ;;
  esac
}

do_restart() {
  do_stop
  sleep 1
  do_start
}

case "${1:-}" in
  run)     do_run ;;
  start)   do_start ;;
  stop)    do_stop ;;
  restart) do_restart ;;
  status)  do_status ;;
  logs)    do_logs "${2:-all}" ;;
  *)
    echo "用法: $0 {run|start|stop|restart|status|logs}"
    echo ""
    echo "命令:"
    echo "  run      前台主管理模式（systemd 推荐）"
    echo "  start    后台启动全部服务"
    echo "  stop     停止全部服务"
    echo "  restart  重启全部服务"
    echo "  status   查看各服务运行状态"
    echo "  logs     查看日志 (logs [backend|outbox|orchestrator|dispatcher|server|loop|all])"
    echo ""
    echo "环境变量:"
    echo "  EDICT_DASHBOARD_HOST  Dashboard 监听地址 (默认: 127.0.0.1)"
    echo "  EDICT_DASHBOARD_PORT  Dashboard 端口 (默认: 7891)"
    echo "  EDICT_BACKEND_PORT    FastAPI Backend 端口 (默认: 8000)"
    exit 1
    ;;
esac
