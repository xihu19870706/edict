#!/usr/bin/env python3
"""
任务状态变更 + 停滞检测 → 自动唤起对应 Agent 的 Watcher

监控 data/tasks_source.json：
1. 状态变化时按阶段自动派发对应 Agent
2. 长时间停滞时自动重试派发

配置文件：data/watcher_config.json
"""
import json
import logging
import os
import pathlib
import signal
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Dict, List

BASE = pathlib.Path(os.environ.get('EDICT_HOME', pathlib.Path(__file__).resolve().parent.parent))
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
if str(BASE / 'scripts') not in sys.path:
    sys.path.insert(0, str(BASE / 'scripts'))

TASKS_FILE = BASE / 'data' / 'tasks_source.json'
PID_FILE = BASE / 'data' / '.agent_watcher_pid'
CONFIG_FILE = BASE / 'data' / 'watcher_config.json'

DEFAULTS = {
    'stall_threshold_sec': 600,
    'max_retry_count': 3,
    'check_interval_sec': 3,
    'log_level': 'INFO',
}
CFG = dict(DEFAULTS)

STATE_AGENT_MAP = {
    'Taizi': 'taizi',
    'Zhongshu': 'zhongshu',
    'Menxia': 'menxia',
    'Assigned': 'shangshu',
    'Pending': 'zhongshu',
    'PendingConfirm': 'shangshu',
    'Review': 'shangshu',
}

log = logging.getLogger('agent_watcher')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [agent_watcher] %(message)s', datefmt='%H:%M:%S')

running = True
last_state: Dict[str, str] = {}
last_retry: Dict[str, float] = {}
retry_count: Dict[str, int] = {}


def load_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
    except Exception:
        return default


def load_config():
    global CFG
    if not CONFIG_FILE.exists():
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    try:
        CFG.update(json.loads(CONFIG_FILE.read_text(encoding='utf-8')))
    except Exception as e:
        log.warning(f'加载配置失败，使用默认值: {e}')


def get_openclaw_bin() -> str:
    return os.environ.get('OPENCLAW_BIN', 'openclaw')


def spawn_agent(agent_id: str, task_id: str, task_title: str, reason: str):
    prompt = f"""📋 自动派发通知
任务ID: {task_id}
任务标题: {task_title}
原因: {reason}

请读取 data/tasks_source.json 中该任务，按三省六部标准链路继续推进。
禁止越级直达 Done；必须遵循：中书省 → 门下省 → 尚书省 → 六部 → 尚书省 → 回奏。
"""
    cmd = [get_openclaw_bin(), 'agent', '--agent', agent_id, '--message', prompt, '--timeout', '600']
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        log.info(f'🚀 已派发 {agent_id} 处理 {task_id} ({reason})')
        return True
    except Exception as e:
        log.error(f'派发失败 {agent_id} {task_id}: {e}')
        return False


def load_tasks() -> List[Dict[str, Any]]:
    return load_json(TASKS_FILE, [])


def check_state_changes(tasks: List[Dict[str, Any]]):
    global last_state
    current = {t.get('id'): t.get('state', '') for t in tasks if t.get('id')}
    changes = []
    for task_id, new_state in current.items():
        old_state = last_state.get(task_id, '')
        if not old_state or old_state == new_state:
            continue
        changes.append((task_id, old_state, new_state))
    last_state = current.copy()
    return changes


def check_stalled(tasks: List[Dict[str, Any]]):
    res = []
    now = time.time()
    for t in tasks:
        task_id = t.get('id')
        state = t.get('state', '')
        if not task_id or state in ('Done', 'Cancelled'):
            continue
        agent_id = STATE_AGENT_MAP.get(state)
        if not agent_id:
            continue
        updated_at = t.get('updatedAt')
        if not updated_at:
            continue
        try:
            ts = datetime.fromisoformat(str(updated_at).replace('Z', '+00:00')).timestamp()
        except Exception:
            continue
        stalled = now - ts
        if stalled < int(CFG['stall_threshold_sec']):
            continue
        if retry_count.get(task_id, 0) >= int(CFG['max_retry_count']):
            continue
        if now - last_retry.get(task_id, 0) < int(CFG['stall_threshold_sec']):
            continue
        res.append((task_id, state, agent_id, int(stalled), t.get('title', '')))
    return res


def ensure_singleton():
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)
            log.info(f'已有 watcher 运行中 pid={old_pid}，本实例退出')
            return False
        except Exception:
            pass
    PID_FILE.write_text(str(os.getpid()), encoding='utf-8')
    return True


def shutdown(signum, frame):
    global running
    running = False
    log.info(f'收到信号 {signum}，准备退出')


def main():
    load_config()
    log.setLevel(getattr(logging, str(CFG.get('log_level', 'INFO')).upper(), logging.INFO))
    if not ensure_singleton():
        return
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    tasks = load_tasks()
    global last_state
    last_state = {t.get('id'): t.get('state', '') for t in tasks if t.get('id')}
    log.info(f'Agent watcher started pid={os.getpid()}，基线任务数={len(last_state)}')
    try:
        while running:
            tasks = load_tasks()
            for task_id, old_state, new_state in check_state_changes(tasks):
                agent_id = STATE_AGENT_MAP.get(new_state)
                if agent_id:
                    title = next((t.get('title','') for t in tasks if t.get('id') == task_id), '')
                    if spawn_agent(agent_id, task_id, title, f'状态变化 {old_state}→{new_state}'):
                        retry_count[task_id] = 0
            for task_id, state, agent_id, stalled_sec, title in check_stalled(tasks):
                if spawn_agent(agent_id, task_id, title, f'停滞 {stalled_sec} 秒自动重试'):
                    last_retry[task_id] = time.time()
                    retry_count[task_id] = retry_count.get(task_id, 0) + 1
                    log.warning(f'🔄 停滞重试 {task_id} {state} 第{retry_count[task_id]}/{CFG["max_retry_count"]}次')
            time.sleep(int(CFG['check_interval_sec']))
    finally:
        try:
            if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink()
        except Exception:
            pass
        log.info('Agent watcher stopped')


if __name__ == '__main__':
    main()
