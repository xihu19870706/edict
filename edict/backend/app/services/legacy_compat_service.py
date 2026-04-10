from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.agents import AGENT_META
from ..api.legacy import _find_by_legacy_id
from ..config import get_settings
from ..models.task import Task, TaskState, TERMINAL_STATES
from ..models.outbox import OutboxEvent

log = logging.getLogger("edict.legacy_compat")
from ..services.event_bus import TOPIC_TASK_CREATED

_AGENT_DEPTS = [
    {"id": "taizi", "label": "太子", "emoji": "🤴", "role": "太子"},
    {"id": "zhongshu", "label": "中书省", "emoji": "📜", "role": "中书令"},
    {"id": "menxia", "label": "门下省", "emoji": "🔍", "role": "侍中"},
    {"id": "shangshu", "label": "尚书省", "emoji": "📮", "role": "尚书令"},
    {"id": "hubu", "label": "户部", "emoji": "💰", "role": "户部尚书"},
    {"id": "libu", "label": "礼部", "emoji": "📝", "role": "礼部尚书"},
    {"id": "bingbu", "label": "兵部", "emoji": "⚔️", "role": "兵部尚书"},
    {"id": "xingbu", "label": "刑部", "emoji": "⚖️", "role": "刑部尚书"},
    {"id": "gongbu", "label": "工部", "emoji": "🔧", "role": "工部尚书"},
    {"id": "libu_hr", "label": "吏部", "emoji": "👔", "role": "吏部尚书"},
    {"id": "zaochao", "label": "钦天监", "emoji": "📰", "role": "朝报官"},
]

_STATE_FLOW = {
    "Pending": ("Taizi", "皇上", "太子", "待处理旨意转交太子分拣"),
    "Taizi": ("Zhongshu", "太子", "中书省", "太子分拣完毕，转中书省起草"),
    "Zhongshu": ("Menxia", "中书省", "门下省", "中书省方案提交门下省审议"),
    "Menxia": ("Assigned", "门下省", "尚书省", "门下省准奏，转尚书省派发"),
    "Assigned": ("Doing", "尚书省", "六部", "尚书省开始派发执行"),
    "Next": ("Doing", "尚书省", "六部", "待执行任务开始执行"),
    "Doing": ("Review", "六部", "尚书省", "各部完成，进入汇总"),
    "Review": ("Done", "尚书省", "太子", "全流程完成，回奏太子转报皇上"),
}

_STATE_LABELS = {
    "Pending": "待处理",
    "Taizi": "太子",
    "Zhongshu": "中书省",
    "Menxia": "门下省",
    "Assigned": "尚书省",
    "Next": "待执行",
    "Doing": "执行中",
    "Review": "审查",
    "Done": "完成",
}

_STATE_AGENT_MAP = {
    "Taizi": "taizi",
    "Zhongshu": "zhongshu",
    "Menxia": "menxia",
    "Assigned": "shangshu",
    "Review": "shangshu",
    "PendingConfirm": "shangshu",
    "Pending": "zhongshu",
}

_ORG_AGENT_MAP = {
    "户部": "hubu",
    "礼部": "libu",
    "兵部": "bingbu",
    "刑部": "xingbu",
    "工部": "gongbu",
    "吏部": "libu_hr",
    "中书省": "zhongshu",
    "门下省": "menxia",
    "尚书省": "shangshu",
}


class LegacyCompatService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._openclaw_home = Path.home() / ".openclaw"

    async def create_task(
        self,
        title: str,
        org: str = "中书省",
        official: str = "中书令",
        priority: str = "中",
        template_id: str = "",
        params: dict | None = None,
        target_dept: str = "",
    ) -> dict:
        """Create a new task using legacy JJC-style ID, matching old dashboard semantics."""
        title = (title or "").strip()
        if not title:
            return {"ok": False, "error": "任务标题不能为空"}
        # Strip conversation metadata markers
        title = re.split(r"\n*Conversation info\s*\(", title, maxsplit=1)[0].strip()
        title = re.split(r"\n*```", title, maxsplit=1)[0].strip()
        title = re.sub(r"^(传旨|下旨)[：:\uff1a]\s*", "", title)
        if len(title) > 100:
            title = title[:100] + "…"
        if len(title) < 6:
            return {"ok": False, "error": f"标题过短（{len(title)}<6），不像是旨意"}

        # Generate JJC-style legacy ID
        today = datetime.datetime.now().strftime("%Y%m%d")
        result = await self.db.execute(select(Task).order_by(Task.created_at.desc()))
        existing_tasks = list(result.scalars().all())
        today_ids = [
            t.task_id
            for t in existing_tasks
            if any(
                isinstance(tag, str) and tag.startswith(f"JJC-{today}-")
                for tag in (t.tags or [])
            )
        ]
        seq = 1
        if today_ids:
            nums = []
            for tid in today_ids:
                parts = str(tid).split("-")
                if len(parts) == 3 and parts[2].isdigit():
                    nums.append(int(parts[2]))
            if nums:
                seq = max(nums) + 1
        legacy_id = f"JJC-{today}-{seq:03d}"

        now = datetime.datetime.now(datetime.timezone.utc)
        trace_id = str(uuid.uuid4())
        task = Task(
            trace_id=trace_id,
            title=title,
            description="等待太子接旨分拣",
            priority=priority,
            state=TaskState.Taizi,
            assignee_org=None,
            creator=official or "emperor",
            tags=[legacy_id],
            org="太子",
            official=official or "emperor",
            now="等待太子接旨分拣",
            target_dept=target_dept or "",
            template_id=template_id or "",
            template_params=params or {},
            flow_log=[
                {
                    "from": None,
                    "to": "Taizi",
                    "agent": "皇上",
                    "reason": "旨意下达",
                    "ts": now.isoformat(),
                }
            ],
            progress_log=[],
            todos=[],
            scheduler={
                "enabled": True,
                "stallThresholdSec": 1800,
                "maxRetry": 5,
                "retryCount": 0,
                "escalationLevel": 0,
                "autoRollback": True,
                "lastProgressAt": now.isoformat(),
                "stallSince": None,
                "lastDispatchStatus": "idle",
                "snapshot": {
                    "state": "Taizi",
                    "org": "太子",
                    "now": "等待太子接旨分拣",
                    "savedAt": now.isoformat(),
                    "note": "create-task-initial",
                },
            },
            meta={
                "legacy_id": legacy_id,
                "original_org": org,
            },
        )
        self.db.add(task)
        await self.db.flush()

        outbox = OutboxEvent(
            topic=TOPIC_TASK_CREATED,
            trace_id=trace_id,
            event_type="task.created",
            producer="legacy_compat",
            payload={
                "task_id": str(task.task_id),
                "title": title,
                "state": "Taizi",
                "priority": priority,
                "assignee_org": None,
                "legacy_id": legacy_id,
            },
        )
        self.db.add(outbox)
        await self.db.commit()
        return {
            "ok": True,
            "taskId": legacy_id,
            "message": f"旨意 {legacy_id} 已下达，正在派发给太子",
        }

    async def get_live_status(self) -> dict:
        tasks = await self._list_tasks()
        items = [self._task_to_legacy_dict(task) for task in tasks]
        total_done = sum(1 for item in items if item.get("state") == "Done")
        in_progress = sum(
            1
            for item in items
            if item.get("state") in {"Doing", "Review", "Next", "Blocked", "Assigned", "Menxia", "Zhongshu"}
        )
        blocked = sum(1 for item in items if item.get("state") == "Blocked")
        return {
            "generatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "taskSource": "backend-compat",
            "officials": [],
            "tasks": items,
            "history": [],
            "metrics": {
                "officialCount": len(AGENT_META),
                "todayDone": 0,
                "totalDone": total_done,
                "inProgress": in_progress,
                "blocked": blocked,
            },
            "syncStatus": {
                "ok": True,
                "source": "backend_compat",
                "lastSyncAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "durationMs": 0,
                "recordCount": len(items),
                "missingFields": {},
                "error": None,
            },
            "health": {
                "syncOk": True,
                "syncLatencyMs": 0,
                "missingFieldCount": 0,
            },
        }

    async def get_agents_status(self) -> dict:
        gateway_alive = self._check_gateway_alive()
        gateway_probe = self._check_gateway_probe() if gateway_alive else False
        agents = []
        for dept in _AGENT_DEPTS:
            agent_id = dept["id"]
            has_workspace = self._check_agent_workspace(agent_id)
            last_ts, session_count, is_busy = self._get_agent_session_status(agent_id)
            process_alive = self._check_agent_process(agent_id)
            if not has_workspace:
                status = "unconfigured"
                status_label = "❌ 未配置"
            elif not gateway_alive:
                status = "offline"
                status_label = "🔴 Gateway 离线"
            elif process_alive or is_busy:
                status = "running"
                status_label = "🟢 运行中"
            elif last_ts > 0:
                now_ms = int(datetime.datetime.now().timestamp() * 1000)
                age_ms = now_ms - last_ts
                if age_ms <= 10 * 60 * 1000:
                    status = "idle"
                    status_label = "🟡 待命"
                elif age_ms <= 3600 * 1000:
                    status = "idle"
                    status_label = "⚪ 空闲"
                else:
                    status = "idle"
                    status_label = "⚪ 休眠"
            else:
                status = "idle"
                status_label = "⚪ 无记录"
            last_active = None
            if last_ts > 0:
                try:
                    last_active = datetime.datetime.fromtimestamp(last_ts / 1000).strftime("%m-%d %H:%M")
                except Exception:
                    last_active = None
            agents.append(
                {
                    "id": agent_id,
                    "label": dept["label"],
                    "emoji": dept["emoji"],
                    "role": dept["role"],
                    "status": status,
                    "statusLabel": status_label,
                    "lastActive": last_active,
                    "lastActiveTs": last_ts,
                    "sessions": session_count,
                    "hasWorkspace": has_workspace,
                    "processAlive": process_alive,
                }
            )
        return {
            "ok": True,
            "gateway": {
                "alive": gateway_alive,
                "probe": gateway_probe,
                "status": "🟢 运行中" if gateway_probe else ("🟡 进程在但无响应" if gateway_alive else "🔴 未启动"),
            },
            "agents": agents,
            "checkedAt": self._now_iso(),
        }

    async def get_scheduler_state(self, legacy_id: str) -> dict:
        task = await self._find_task(legacy_id)
        if not task:
            return {"ok": False, "error": f"任务 {legacy_id} 不存在"}
        scheduler = self._ensure_scheduler(task)
        last_progress = self._parse_iso(scheduler.get("lastProgressAt") or self._task_updated_iso(task))
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        stalled_sec = 0
        if last_progress:
            stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
        await self.db.commit()
        return {
            "ok": True,
            "taskId": legacy_id,
            "state": self._state_value(task),
            "org": task.org or "",
            "scheduler": scheduler,
            "stalledSec": stalled_sec,
            "checkedAt": self._now_iso(),
        }

    async def review_action(self, legacy_id: str, action: str, comment: str = "") -> dict:
        task = await self._find_task(legacy_id)
        if not task:
            return {"ok": False, "error": f"任务 {legacy_id} 不存在"}
        state = self._state_value(task)
        if state not in {"Review", "Menxia"}:
            return {"ok": False, "error": f"任务 {legacy_id} 当前状态为 {state}，无法御批"}
        self._ensure_scheduler(task)
        self._scheduler_snapshot(task, f"review-before-{action}")
        if action == "approve":
            if state == "Menxia":
                task.state = TaskState.Assigned
                task.org = "尚书省"
                task.now = "门下省准奏，移交尚书省派发"
                remark = f"✅ 准奏：{comment or '门下省审议通过'}"
                to_dept = "尚书省"
            else:
                task.state = TaskState.Done
                task.org = "皇上"
                task.now = "御批通过，任务完成"
                remark = f"✅ 御批准奏：{comment or '审查通过'}"
                to_dept = "皇上"
        elif action == "reject":
            meta = dict(task.meta or {})
            round_num = int(meta.get("review_round") or 0) + 1
            meta["review_round"] = round_num
            task.meta = meta
            task.state = TaskState.Zhongshu
            task.org = "中书省"
            task.now = f"封驳退回中书省修订（第{round_num}轮）"
            remark = f"🚫 封驳：{comment or '需要修改'}"
            to_dept = "中书省"
        else:
            return {"ok": False, "error": f"未知操作: {action}"}
        from_dept = "皇上" if self._state_value(task) == "Done" else "门下省"
        self._append_flow(task, {"at": self._now_iso(), "from": from_dept, "to": to_dept, "remark": remark})
        self._scheduler_mark_progress(task, f"审议动作 {action} -> {self._state_value(task)}")
        self._mark_updated(task)
        if self._state_value(task) != "Done":
            self._mark_dispatch(task, self._state_value(task), "legacy-review-action")
        await self.db.commit()
        label = "已准奏" if action == "approve" else "已封驳"
        dispatched = " (已自动派发 Agent)" if self._state_value(task) != "Done" else ""
        return {"ok": True, "message": f"{legacy_id} {label}{dispatched}"}

    async def advance_state(self, legacy_id: str, comment: str = "") -> dict:
        task = await self._find_task(legacy_id)
        if not task:
            return {"ok": False, "error": f"任务 {legacy_id} 不存在"}
        current = self._state_value(task)
        if current not in _STATE_FLOW:
            return {"ok": False, "error": f"任务 {legacy_id} 状态为 {current}，无法推进"}
        self._ensure_scheduler(task)
        self._scheduler_snapshot(task, f"advance-before-{current}")
        next_state, from_dept, to_dept, default_remark = _STATE_FLOW[current]
        remark = comment or default_remark
        task.state = TaskState(next_state)
        task.org = to_dept
        task.now = f"⬇️ 手动推进：{remark}"
        self._append_flow(
            task,
            {"at": self._now_iso(), "from": from_dept, "to": to_dept, "remark": f"⬇️ 手动推进：{remark}"},
        )
        self._scheduler_mark_progress(task, f"手动推进 {current} -> {next_state}")
        self._mark_updated(task)
        if next_state != "Done":
            self._mark_dispatch(task, next_state, "legacy-advance-state")
        await self.db.commit()
        dispatched = " (已自动派发 Agent)" if next_state != "Done" else ""
        return {
            "ok": True,
            "message": f"{legacy_id} {_STATE_LABELS.get(current, current)} → {_STATE_LABELS.get(next_state, next_state)}{dispatched}",
        }

    async def scheduler_scan(self, threshold_sec: int = 600) -> dict:
        threshold_sec = max(60, int(threshold_sec or 600))
        tasks = await self._list_tasks()
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        actions: list[dict] = []
        changed = False
        for task in tasks:
            state = self._state_value(task)
            if state in {s.value for s in TERMINAL_STATES} or state == "Blocked" or task.archived:
                continue
            scheduler = self._ensure_scheduler(task)
            task_threshold = int(scheduler.get("stallThresholdSec") or threshold_sec)
            last_progress = self._parse_iso(scheduler.get("lastProgressAt") or self._task_updated_iso(task))
            if not last_progress:
                continue
            stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
            if stalled_sec < task_threshold:
                continue
            if not scheduler.get("stallSince"):
                scheduler["stallSince"] = self._now_iso()
                task.scheduler = dict(scheduler)
                changed = True
            retry_count = int(scheduler.get("retryCount") or 0)
            max_retry = max(0, int(scheduler.get("maxRetry") or 1))
            level = int(scheduler.get("escalationLevel") or 0)
            legacy_id = self._legacy_id(task)
            if retry_count < max_retry:
                scheduler["retryCount"] = retry_count + 1
                scheduler["lastRetryAt"] = self._now_iso()
                scheduler["lastDispatchTrigger"] = "taizi-scan-retry"
                scheduler["lastDispatchStatus"] = "queued"
                task.scheduler = dict(scheduler)
                self._append_scheduler_flow(task, f"停滞{stalled_sec}秒，触发自动重试第{scheduler['retryCount']}次")
                actions.append({"taskId": legacy_id, "action": "retry", "stalledSec": stalled_sec})
                changed = True
                continue
            if level < 2:
                next_level = level + 1
                target_label = "门下省" if next_level == 1 else "尚书省"
                scheduler["escalationLevel"] = next_level
                scheduler["lastEscalatedAt"] = self._now_iso()
                task.scheduler = dict(scheduler)
                self._append_scheduler_flow(task, f"停滞{stalled_sec}秒，升级至{target_label}协调", to=target_label)
                actions.append({"taskId": legacy_id, "action": "escalate", "to": target_label, "stalledSec": stalled_sec})
                changed = True
                continue
            if scheduler.get("autoRollback", True):
                rollback_count = int(scheduler.get("rollbackCount") or 0)
                max_rollback = int(scheduler.get("maxRollback") or 3)
                snapshot = scheduler.get("snapshot") or {}
                snap_state = snapshot.get("state")
                if rollback_count >= max_rollback:
                    task.state = TaskState.Blocked
                    task.now = f"🚫 连续回滚{rollback_count}次仍无法推进，已自动挂起"
                    task.block = f"连续停滞且回滚{rollback_count}次均失败，需人工介入"
                    scheduler["stallSince"] = None
                    task.scheduler = dict(scheduler)
                    self._append_scheduler_flow(task, f"连续回滚{rollback_count}次，自动挂起等待人工介入")
                    actions.append({"taskId": legacy_id, "action": "blocked", "reason": f"max rollback {rollback_count}"})
                    changed = True
                elif snap_state and snap_state != state:
                    task.state = TaskState(snap_state)
                    task.org = snapshot.get("org", task.org or "")
                    task.now = "↩️ 太子调度自动回滚到稳定节点"
                    task.block = "无"
                    scheduler["retryCount"] = 0
                    scheduler["escalationLevel"] = 0
                    scheduler["rollbackCount"] = rollback_count + 1
                    scheduler["stallSince"] = None
                    scheduler["lastProgressAt"] = self._now_iso()
                    scheduler["lastDispatchStatus"] = "queued"
                    task.scheduler = dict(scheduler)
                    self._append_scheduler_flow(task, f"连续停滞，自动回滚：{state} → {snap_state}（第{rollback_count + 1}次）")
                    actions.append({"taskId": legacy_id, "action": "rollback", "toState": snap_state})
                    changed = True
                self._mark_updated(task)
        if changed:
            await self.db.commit()
        return {
            "ok": True,
            "thresholdSec": threshold_sec,
            "actions": actions,
            "count": len(actions),
            "checkedAt": self._now_iso(),
        }

    async def get_task_activity(self, legacy_id: str) -> dict:
        task = await self._find_task(legacy_id)
        if not task:
            return {"ok": False, "error": f"任务 {legacy_id} 不存在"}
        scheduler = self._ensure_scheduler(task)
        data = task.to_dict()
        data["id"] = legacy_id
        data["_scheduler"] = scheduler
        data["heartbeat"] = self._heartbeat_for_task(data)
        data["outputMeta"] = self._output_meta(data.get("output", ""))
        flow_log = data.get("flow_log", [])
        progress_log = data.get("progress_log", [])
        return {
            "ok": True,
            "taskId": legacy_id,
            "state": self._state_value(task),
            "org": task.org or "",
            "now": task.now or "",
            "updatedAt": self._task_updated_iso(task),
            "heartbeat": data["heartbeat"],
            "flow_log": flow_log[-20:],
            "progress_log": progress_log[-10:],
            "activity": flow_log[-5:] + progress_log[-5:],
            "scheduler": scheduler,
            "outputMeta": data["outputMeta"],
        }

    async def archive_task(self, legacy_id: str, archived: bool) -> dict:
        task = await self._find_task(legacy_id)
        if not task:
            return {"ok": False, "error": f"任务 {legacy_id} 不存在"}
        task.archived = archived
        if archived:
            task.archived_at = datetime.datetime.now(datetime.timezone.utc)
        else:
            task.archived_at = None
        self._mark_updated(task)
        await self.db.commit()
        label = "已归档" if archived else "已取消归档"
        return {"ok": True, "message": f"{legacy_id} {label}"}

    async def scheduler_retry(self, legacy_id: str, reason: str = "") -> dict:
        task = await self._find_task(legacy_id)
        if not task:
            return {"ok": False, "error": f"任务 {legacy_id} 不存在"}
        state = self._state_value(task)
        if state in {s.value for s in TERMINAL_STATES} or state == "Blocked":
            return {"ok": False, "error": f"任务 {legacy_id} 当前状态 {state} 不支持重试"}
        scheduler = self._ensure_scheduler(task)
        scheduler["retryCount"] = int(scheduler.get("retryCount") or 0) + 1
        scheduler["lastRetryAt"] = self._now_iso()
        scheduler["lastDispatchTrigger"] = "taizi-retry"
        scheduler["lastDispatchStatus"] = "queued"
        task.scheduler = dict(scheduler)
        self._append_scheduler_flow(task, f"触发重试第{scheduler['retryCount']}次：{reason or '超时未推进'}")
        self._mark_updated(task)
        await self.db.commit()
        return {
            "ok": True,
            "message": f"{legacy_id} 已触发重试派发",
            "retryCount": scheduler["retryCount"],
        }

    async def scheduler_escalate(self, legacy_id: str, reason: str = "") -> dict:
        task = await self._find_task(legacy_id)
        if not task:
            return {"ok": False, "error": f"任务 {legacy_id} 不存在"}
        state = self._state_value(task)
        if state in {s.value for s in TERMINAL_STATES}:
            return {"ok": False, "error": f"任务 {legacy_id} 已结束，无需升级"}
        scheduler = self._ensure_scheduler(task)
        current_level = int(scheduler.get("escalationLevel") or 0)
        next_level = min(current_level + 1, 2)
        target_label = "门下省" if next_level == 1 else "尚书省"
        scheduler["escalationLevel"] = next_level
        scheduler["lastEscalatedAt"] = self._now_iso()
        task.scheduler = dict(scheduler)
        self._append_scheduler_flow(task, f"升级到{target_label}协调：{reason or '任务停滞'}", to=target_label)
        self._mark_updated(task)
        await self.db.commit()
        return {
            "ok": True,
            "message": f"{legacy_id} 已升级至{target_label}",
            "escalationLevel": next_level,
        }

    async def scheduler_rollback(self, legacy_id: str, reason: str = "") -> dict:
        task = await self._find_task(legacy_id)
        if not task:
            return {"ok": False, "error": f"任务 {legacy_id} 不存在"}
        scheduler = self._ensure_scheduler(task)
        snapshot = scheduler.get("snapshot") or {}
        snap_state = snapshot.get("state")
        if not snap_state:
            return {"ok": False, "error": f"任务 {legacy_id} 无可用回滚快照"}
        old_state = self._state_value(task)
        task.state = TaskState(snap_state)
        task.org = snapshot.get("org", task.org or "")
        task.now = f"↩️ 太子调度自动回滚：{reason or '恢复到上个稳定节点'}"
        task.block = "无"
        scheduler["retryCount"] = 0
        scheduler["escalationLevel"] = 0
        scheduler["stallSince"] = None
        scheduler["lastProgressAt"] = self._now_iso()
        scheduler["lastDispatchStatus"] = "queued"
        task.scheduler = dict(scheduler)
        self._append_scheduler_flow(
            task,
            f"执行回滚：{old_state} → {snap_state}，原因：{reason or '停滞恢复'}",
        )
        self._mark_updated(task)
        await self.db.commit()
        return {"ok": True, "message": f"{legacy_id} 已回滚到 {snap_state}"}

    async def task_action(self, legacy_id: str, action: str, reason: str = "") -> dict:
        """Handle stop/cancel/resume actions on a task."""
        task = await self._find_task(legacy_id)
        if not task:
            return {"ok": False, "error": f"任务 {legacy_id} 不存在"}
        old_state = self._state_value(task)
        self._ensure_scheduler(task)
        self._scheduler_snapshot(task, f"task-action-before-{action}")
        if action == "stop":
            task.state = TaskState.Blocked
            task.block = reason or "皇上叫停"
            task.now = f"⏸️ 已暂停：{reason}"
        elif action == "cancel":
            task.state = TaskState.Cancelled
            task.block = reason or "皇上取消"
            task.now = f"🚫 已取消：{reason}"
        elif action == "resume":
            prev = task.meta.get("_prev_state") if isinstance(task.meta, dict) else None
            task.state = TaskState(prev) if prev else TaskState.Doing
            task.block = "无"
            task.now = f"▶️ 已恢复执行"
        else:
            return {"ok": False, "error": f"未知操作: {action}"}
        meta = dict(task.meta or {})
        if action in ("stop", "cancel"):
            meta["_prev_state"] = old_state
        elif action == "resume":
            self._scheduler_mark_progress(task, f"恢复到 {self._state_value(task)}")
        task.meta = meta
        self._append_flow(
            task,
            {
                "at": self._now_iso(),
                "from": "皇上",
                "to": task.org or "",
                "remark": f"{'⏸️ 叫停' if action == 'stop' else '🚫 取消' if action == 'cancel' else '▶️ 恢复'}：{reason}",
            },
        )
        self._mark_updated(task)
        if action == "resume" and self._state_value(task) not in {s.value for s in TERMINAL_STATES}:
            self._mark_dispatch(task, self._state_value(task), "legacy-resume")
        await self.db.commit()
        label = {"stop": "已叫停", "cancel": "已取消", "resume": "已恢复"}[action]
        return {"ok": True, "message": f"{legacy_id} {label}"}

    # ── Skill / Agent Config APIs ──────────────────────────────────────────────

    def _data_dir(self) -> Path:
        settings = get_settings()
        return Path(settings.legacy_data_dir).resolve()

    def _safe_name(self, s: str) -> bool:
        return bool(re.match(r"^[a-zA-Z0-9_\-\u4e00-\u9fff]+$", s))

    def _sync_agent_config(self) -> None:
        """Run sync_agent_config.py to re-sync agent config."""
        try:
            scripts_dir = self._data_dir().parent / "scripts"
            subprocess.run(
                ["python3", str(scripts_dir / "sync_agent_config.py")],
                timeout=10,
                check=False,
            )
        except Exception:
            pass

    def get_agent_config(self) -> dict:
        cfg = self._read_json(self._data_dir() / "agent_config.json", {})
        return cfg

    def set_model(self, agent_id: str, model: str) -> dict:
        """Queue a model change for an agent."""
        if not self._safe_name(agent_id) or not model:
            return {"ok": False, "error": "agentId and model required"}
        pending = self._data_dir() / "pending_model_changes.json"
        current = self._read_json(pending, [])
        if not isinstance(current, list):
            current = []
        current = [x for x in current if x.get("agentId") != agent_id]
        current.append({"agentId": agent_id, "model": model})
        self._write_json(pending, current)
        # Async apply
        def apply_async():
            try:
                scripts_dir = self._data_dir().parent / "scripts"
                subprocess.run(["python3", str(scripts_dir / "apply_model_changes.py")], timeout=30, check=False)
                self._sync_agent_config()
            except Exception:
                pass
        import threading
        threading.Thread(target=apply_async, daemon=True).start()
        return {"ok": True, "message": f"Queued: {agent_id} → {model}"}

    def get_model_change_log(self) -> dict:
        return self._read_json(self._data_dir() / "model_change_log.json", [])

    def set_dispatch_channel(self, channel: str) -> dict:
        allowed = {"feishu", "telegram", "wecom", "signal", "tui", "discord", "slack"}
        if channel not in allowed:
            return {"ok": False, "error": f"channel must be one of: {', '.join(sorted(allowed))}"}
        cfg = self._read_json(self._data_dir() / "agent_config.json", {})
        cfg["dispatchChannel"] = channel
        self._write_json(self._data_dir() / "agent_config.json", cfg)
        return {"ok": True, "message": f"派发渠道已切换为 {channel}"}

    def get_remote_skills_list(self) -> dict:
        remote_skills = []
        openclaw_home = self._openclaw_home()
        for ws_dir in openclaw_home.glob("workspace-*"):
            agent_id = ws_dir.name.replace("workspace-", "")
            skills_dir = ws_dir / "skills"
            if not skills_dir.is_dir():
                continue
            for skill_dir in skills_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_name = skill_dir.name
                source_json = skill_dir / ".source.json"
                skill_md = skill_dir / "SKILL.md"
                if not source_json.exists():
                    continue
                try:
                    source_info = json.loads(source_json.read_text())
                    status = "valid" if skill_md.exists() else "not-found"
                    remote_skills.append({
                        "skillName": skill_name,
                        "agentId": agent_id,
                        "sourceUrl": source_info.get("sourceUrl", ""),
                        "description": source_info.get("description", ""),
                        "localPath": str(skill_md),
                        "addedAt": source_info.get("addedAt", ""),
                        "lastUpdated": source_info.get("lastUpdated", ""),
                        "status": status,
                    })
                except Exception:
                    pass
        return {
            "ok": True,
            "remoteSkills": remote_skills,
            "count": len(remote_skills),
            "listedAt": self._now_iso(),
        }

    def read_skill_content(self, agent_id: str, skill_name: str) -> dict:
        if not self._safe_name(agent_id) or not self._safe_name(skill_name):
            return {"ok": False, "error": "参数含非法字符"}
        cfg = self._read_json(self._data_dir() / "agent_config.json", {})
        agents = cfg.get("agents", [])
        ag = next((a for a in agents if a.get("id") == agent_id), None)
        if not ag:
            return {"ok": False, "error": f"Agent {agent_id} 不存在"}
        sk = next((s for s in ag.get("skills", []) if s.get("name") == skill_name), None)
        if not sk:
            return {"ok": False, "error": f"技能 {skill_name} 不存在"}
        skill_path = Path(sk.get("path", "")).resolve()
        openclaw_home = self._openclaw_home().resolve()
        project_root = self._data_dir().parent.resolve()
        if not any(str(skill_path).startswith(str(root)) for root in (openclaw_home, project_root)):
            return {"ok": False, "error": "路径不在允许的目录范围内"}
        if not skill_path.exists():
            return {"ok": True, "name": skill_name, "agent": agent_id, "content": "(SKILL.md 文件不存在)", "path": str(skill_path)}
        try:
            content = skill_path.read_text()
            return {"ok": True, "name": skill_name, "agent": agent_id, "content": content, "path": str(skill_path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def add_skill(self, agent_id: str, skill_name: str, description: str = "", trigger: str = "") -> dict:
        if not self._safe_name(skill_name) or not self._safe_name(agent_id):
            return {"ok": False, "error": "skillName 或 agentId 含非法字符"}
        openclaw_home = self._openclaw_home()
        workspace = openclaw_home / f"workspace-{agent_id}" / "skills" / skill_name
        workspace.mkdir(parents=True, exist_ok=True)
        skill_md = workspace / "SKILL.md"
        desc_line = description or skill_name
        trigger_section = f"\n## 触发条件\n{trigger}\n" if trigger else ""
        template = (
            f"---\n"
            f"name: {skill_name}\n"
            f"description: {desc_line}\n"
            f"---\n\n"
            f"# {skill_name}\n\n"
            f"{desc_line}\n"
            f"{trigger_section}"
            f"## 输入\n\n"
            f"<!-- 说明此技能接收什么输入 -->\n\n"
            f"## 处理流程\n\n"
            f"1. 步骤一\n"
            f"2. 步骤二\n\n"
            f"## 输出规范\n\n"
            f"<!-- 说明产出物格式与交付要求 -->\n\n"
            f"## 注意事项\n\n"
            f"- (在此补充约束、限制或特殊规则)\n"
        )
        skill_md.write_text(template, encoding="utf-8")
        self._sync_agent_config()
        return {"ok": True, "message": f"技能 {skill_name} 已添加到 {agent_id}", "path": str(skill_md)}

    def _fetch_url_content(self, url: str, max_size: int = 10 * 1024 * 1024) -> str | None:
        """Fetch content from URL, returns None on failure."""
        try:
            req = Request(url, headers={"User-Agent": "OpenClaw-SkillManager/1.0"})
            resp = urlopen(req, timeout=10)
            content = resp.read(max_size).decode("utf-8")
            if len(content) > max_size:
                return None
            return content
        except Exception:
            return None

    def _validate_url(self, url: str) -> bool:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.scheme in ("https",) and bool(parsed.netloc)

    def _skill_validate_frontmatter(self, content: str) -> bool:
        if not content.startswith("---"):
            return False
        parts = content.split("---", 2)
        if len(parts) < 3:
            return False
        if "name:" not in content[:500]:
            return False
        try:
            yaml.safe_load(parts[1])
            return True
        except Exception:
            return True  # String check passed, proceed anyway

    def add_remote_skill(self, agent_id: str, skill_name: str, source_url: str, description: str = "") -> dict:
        if not self._safe_name(agent_id):
            return {"ok": False, "error": f"agentId 含非法字符"}
        if not self._safe_name(skill_name):
            return {"ok": False, "error": f"skillName 含非法字符"}
        source_url = source_url.strip()
        if not source_url:
            return {"ok": False, "error": "sourceUrl 必须是有效的字符串"}

        cfg = self._read_json(self._data_dir() / "agent_config.json", {})
        agents = cfg.get("agents", [])
        if not any(a.get("id") == agent_id for a in agents):
            return {"ok": False, "error": f"Agent {agent_id} 不存在"}

        content: str | None = None
        if source_url.startswith("http://") or source_url.startswith("https://"):
            if not self._validate_url(source_url):
                return {"ok": False, "error": "URL 无效或不安全（仅支持 HTTPS）"}
            content = self._fetch_url_content(source_url)
            if content is None:
                return {"ok": False, "error": "URL 无法访问"}
        elif source_url.startswith("file://"):
            local_path = Path(source_url[7:]).resolve()
            openclaw_home = self._openclaw_home().resolve()
            project_root = self._data_dir().parent.resolve()
            if not any(str(local_path).startswith(str(root)) for root in (openclaw_home, project_root)):
                return {"ok": False, "error": "路径不在允许的目录范围内"}
            if not local_path.exists():
                return {"ok": False, "error": f"本地文件不存在: {local_path}"}
            try:
                content = local_path.read_text()
            except Exception as e:
                return {"ok": False, "error": f"文件读取失败: {e}"}
        elif source_url.startswith("/") or source_url.startswith("."):
            local_path = Path(source_url).resolve()
            openclaw_home = self._openclaw_home().resolve()
            project_root = self._data_dir().parent.resolve()
            if not any(str(local_path).startswith(str(root)) for root in (openclaw_home, project_root)):
                return {"ok": False, "error": "路径不在允许的目录范围内"}
            if not local_path.exists():
                return {"ok": False, "error": f"本地文件不存在: {local_path}"}
            try:
                content = local_path.read_text()
            except Exception as e:
                return {"ok": False, "error": f"文件读取失败: {e}"}
        else:
            return {"ok": False, "error": "不支持的 URL 格式（仅支持 https://, file://, 或本地路径）"}

        if content is None:
            return {"ok": False, "error": "文件内容获取失败"}

        if not self._skill_validate_frontmatter(content):
            return {"ok": False, "error": "文件格式无效（缺少 YAML frontmatter 或 frontmatter 缺少 name 字段）"}

        openclaw_home = self._openclaw_home()
        workspace = openclaw_home / f"workspace-{agent_id}" / "skills" / skill_name
        workspace.mkdir(parents=True, exist_ok=True)
        skill_md = workspace / "SKILL.md"
        skill_md.write_text(content, encoding="utf-8")

        source_info = {
            "skillName": skill_name,
            "sourceUrl": source_url,
            "description": description,
            "addedAt": self._now_iso(),
            "lastUpdated": self._now_iso(),
            "checksum": hashlib.sha256(content.encode()).hexdigest()[:16],
            "status": "valid",
        }
        (workspace / ".source.json").write_text(json.dumps(source_info, ensure_ascii=False, indent=2), encoding="utf-8")
        self._sync_agent_config()
        return {
            "ok": True,
            "message": f"技能 {skill_name} 已从远程源添加到 {agent_id}",
            "skillName": skill_name,
            "agentId": agent_id,
            "source": source_url,
            "localPath": str(skill_md),
            "size": len(content),
            "addedAt": source_info["addedAt"],
        }

    def update_remote_skill(self, agent_id: str, skill_name: str) -> dict:
        if not self._safe_name(agent_id) or not self._safe_name(skill_name):
            return {"ok": False, "error": "agentId 或 skillName 含非法字符"}
        workspace = self._openclaw_home() / f"workspace-{agent_id}" / "skills" / skill_name
        source_json = workspace / ".source.json"
        if not source_json.exists():
            return {"ok": False, "error": f"技能 {skill_name} 不是远程 skill（无 .source.json）"}
        try:
            source_info = json.loads(source_json.read_text())
            source_url = source_info.get("sourceUrl", "")
            if not source_url:
                return {"ok": False, "error": "源 URL 不存在"}
            result = self.add_remote_skill(agent_id, skill_name, source_url, source_info.get("description", ""))
            if result["ok"]:
                result["message"] = "技能已更新"
                new_info = json.loads((workspace / ".source.json").read_text())
                result["newVersion"] = new_info.get("checksum", "unknown")
            return result
        except Exception as e:
            return {"ok": False, "error": f"更新失败: {str(e)[:100]}"}

    def remove_remote_skill(self, agent_id: str, skill_name: str) -> dict:
        if not self._safe_name(agent_id) or not self._safe_name(skill_name):
            return {"ok": False, "error": "agentId 或 skillName 含非法字符"}
        workspace = self._openclaw_home() / f"workspace-{agent_id}" / "skills" / skill_name
        if not workspace.exists():
            return {"ok": False, "error": f"技能不存在: {skill_name}"}
        source_json = workspace / ".source.json"
        if not source_json.exists():
            return {"ok": False, "error": f"技能 {skill_name} 不是远程 skill，无法通过此 API 移除"}
        try:
            shutil.rmtree(workspace)
            self._sync_agent_config()
            return {"ok": True, "message": f"技能 {skill_name} 已从 {agent_id} 移除"}
        except Exception as e:
            return {"ok": False, "error": f"移除失败: {str(e)[:100]}"}

    def _read_json(self, path: Path, default) -> dict | list:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return default

    def _write_json(self, path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _find_task(self, legacy_id: str) -> Task | None:
        return await _find_by_legacy_id(self.db, legacy_id)

    async def _list_tasks(self) -> list[Task]:
        result = await self.db.execute(select(Task).order_by(Task.created_at.desc()))
        return list(result.scalars().all())

    def _task_to_legacy_dict(self, task: Task) -> dict:
        data = task.to_dict()
        legacy_id = self._legacy_id(task)
        data["id"] = legacy_id
        data["_scheduler"] = self._ensure_scheduler(task)
        data["scheduler"] = data["_scheduler"]
        data["outputMeta"] = self._output_meta(data.get("output", ""))
        data["heartbeat"] = self._heartbeat_for_task(data)
        return data

    def _legacy_id(self, task: Task) -> str:
        meta = task.meta or {}
        legacy_id = meta.get("legacy_id")
        if legacy_id:
            return str(legacy_id)
        for tag in task.tags or []:
            if isinstance(tag, str) and tag:
                return tag
        return str(task.task_id)

    def _ensure_scheduler(self, task: Task) -> dict:
        scheduler = dict(task.scheduler or {})
        scheduler.setdefault("enabled", True)
        scheduler.setdefault("stallThresholdSec", 1800)
        scheduler.setdefault("maxRetry", 5)
        scheduler.setdefault("retryCount", 0)
        scheduler.setdefault("escalationLevel", 0)
        scheduler.setdefault("autoRollback", True)
        scheduler.setdefault("lastProgressAt", self._task_updated_iso(task) or self._now_iso())
        scheduler.setdefault("stallSince", None)
        scheduler.setdefault("lastDispatchStatus", "idle")
        scheduler.setdefault(
            "snapshot",
            {
                "state": self._state_value(task),
                "org": task.org or "",
                "now": task.now or "",
                "savedAt": self._now_iso(),
                "note": "init",
            },
        )
        task.scheduler = scheduler
        return scheduler

    def _scheduler_snapshot(self, task: Task, note: str = "") -> None:
        scheduler = self._ensure_scheduler(task)
        scheduler["snapshot"] = {
            "state": self._state_value(task),
            "org": task.org or "",
            "now": task.now or "",
            "savedAt": self._now_iso(),
            "note": note or "snapshot",
        }
        task.scheduler = dict(scheduler)

    def _scheduler_mark_progress(self, task: Task, note: str = "") -> None:
        scheduler = self._ensure_scheduler(task)
        scheduler["lastProgressAt"] = self._now_iso()
        scheduler["stallSince"] = None
        scheduler["retryCount"] = 0
        scheduler["escalationLevel"] = 0
        scheduler["rollbackCount"] = 0
        scheduler["lastEscalatedAt"] = None
        task.scheduler = dict(scheduler)
        if note:
            self._append_scheduler_flow(task, f"进展确认：{note}")

    def _append_scheduler_flow(self, task: Task, remark: str, to: str = "") -> None:
        self._append_flow(
            task,
            {"at": self._now_iso(), "from": "太子调度", "to": to or (task.org or ""), "remark": f"🧭 {remark}"},
        )

    def _append_flow(self, task: Task, entry: dict) -> None:
        flow_log = list(task.flow_log or [])
        flow_log.append(entry)
        task.flow_log = flow_log

    def _mark_dispatch(self, task: Task, state: str, trigger: str) -> None:
        scheduler = self._ensure_scheduler(task)
        agent_id = _STATE_AGENT_MAP.get(state)
        if agent_id is None and state in {"Doing", "Next"}:
            agent_id = _ORG_AGENT_MAP.get(task.org or "")
        scheduler["lastDispatchAt"] = self._now_iso()
        scheduler["lastDispatchStatus"] = "queued" if agent_id else "idle"
        scheduler["lastDispatchAgent"] = agent_id or ""
        scheduler["lastDispatchTrigger"] = trigger
        task.scheduler = dict(scheduler)
        if agent_id:
            self._append_scheduler_flow(task, f"已入队派发：{state} → {agent_id}（{trigger}）", to=task.org or state)

    def _mark_updated(self, task: Task) -> None:
        task.updated_at = datetime.datetime.now(datetime.timezone.utc)

    def _state_value(self, task: Task) -> str:
        return task.state.value if isinstance(task.state, TaskState) else str(task.state or "")

    def _task_updated_iso(self, task: Task) -> str:
        return task.updated_at.isoformat() if task.updated_at else ""

    def _heartbeat_for_task(self, task: dict) -> dict | None:
        state = task.get("state")
        if state not in {"Doing", "Assigned", "Review"}:
            return None
        updated_raw = task.get("updatedAt") or task.get("updated_at")
        age_sec = None
        if updated_raw:
            try:
                updated_dt = datetime.datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
                if updated_dt.tzinfo is None:
                    updated_dt = updated_dt.replace(tzinfo=datetime.timezone.utc)
                age_sec = (datetime.datetime.now(datetime.timezone.utc) - updated_dt).total_seconds()
            except Exception:
                age_sec = None
        if age_sec is None:
            return {"status": "unknown", "label": "⚪ 未知", "ageSec": None}
        if age_sec < 300:
            return {"status": "active", "label": f"🟢 活跃 {int(age_sec // 60)}分钟前", "ageSec": int(age_sec)}
        if age_sec < 900:
            return {"status": "warn", "label": f"🟡 可能停滞 {int(age_sec // 60)}分钟前", "ageSec": int(age_sec)}
        return {"status": "stalled", "label": f"🔴 已停滞 {int(age_sec // 60)}分钟", "ageSec": int(age_sec)}

    def _output_meta(self, path: str) -> dict:
        if not path:
            return {"exists": False, "lastModified": None}
        output = Path(path)
        if not output.exists():
            return {"exists": False, "lastModified": None}
        ts = datetime.datetime.fromtimestamp(output.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        return {"exists": True, "lastModified": ts}

    def _check_gateway_alive(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 18789), timeout=2):
                return True
        except Exception:
            pass
        try:
            if os.name == "nt":
                return False
            result = subprocess.run(
                ["pgrep", "-f", "openclaw-gateway"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _check_gateway_probe(self) -> bool:
        for url in ("http://127.0.0.1:18789/", "http://127.0.0.1:18789/healthz"):
            try:
                from urllib.request import urlopen

                response = urlopen(url, timeout=3)
                if 200 <= response.status < 500:
                    return True
            except Exception:
                continue
        return False

    def _check_agent_workspace(self, agent_id: str) -> bool:
        return (self._openclaw_home / f"workspace-{agent_id}").is_dir()

    def _get_agent_session_status(self, agent_id: str) -> tuple[int, int, bool]:
        sessions_file = self._openclaw_home / "agents" / agent_id / "sessions" / "sessions.json"
        if not sessions_file.exists():
            return 0, 0, False
        try:
            data = json.loads(sessions_file.read_text())
        except Exception:
            return 0, 0, False
        if not isinstance(data, dict):
            return 0, 0, False
        session_count = len(data)
        last_ts = 0
        for value in data.values():
            ts = value.get("updatedAt", 0)
            if isinstance(ts, (int, float)) and ts > last_ts:
                last_ts = int(ts)
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        age_ms = now_ms - last_ts if last_ts else 9999999999
        return last_ts, session_count, age_ms <= 2 * 60 * 1000

    def _check_agent_process(self, agent_id: str) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"openclaw.*--agent.*{agent_id}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _parse_iso(self, value: str | None) -> datetime.datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed

    def _now_iso(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    # ── Agent Wake ─────────────────────────────────────────────────────────────

    def _safe_name_re(self) -> re.Pattern:
        return re.compile(r"^[a-zA-Z0-9_\-\u4e00-\u9fff]+$")

    def wake_agent(self, agent_id: str, message: str = "") -> dict:
        """唤醒指定 Agent，发送心跳检测消息。"""
        if not self._safe_name_re().match(agent_id):
            return {"ok": False, "error": f"agent_id 非法: {agent_id}"}
        if not self._check_agent_workspace(agent_id):
            return {"ok": False, "error": f"{agent_id} 工作空间不存在，请先配置"}
        if not self._check_gateway_alive():
            return {"ok": False, "error": "Gateway 未启动，请先运行 openclaw gateway start"}
        msg = message or f"🔔 系统心跳检测 — 请回复 OK 确认在线。当前时间: {self._now_iso()}"
        import threading

        def do_wake():
            for attempt in range(1, 3):
                try:
                    result = subprocess.run(
                        ["openclaw", "agent", "--agent", agent_id, "-m", msg, "--timeout", "120"],
                        capture_output=True,
                        text=True,
                        timeout=620,
                    )
                    if result.returncode == 0:
                        return
                    err = (result.stderr or result.stdout or "")[:200]
                    log.warning(f"⚠️ {agent_id} 唤醒失败(第{attempt}次): {err}")
                    if attempt < 2:
                        time.sleep(5)
                except subprocess.TimeoutExpired:
                    log.error(f"❌ {agent_id} 唤醒超时")
                except Exception as e:
                    log.warning(f"⚠️ {agent_id} 唤醒异常: {e}")

        threading.Thread(target=do_wake, daemon=True).start()
        return {"ok": True, "message": f"{agent_id} 唤醒指令已发出，约10-30秒后生效"}

    # ── File-based read endpoints ─────────────────────────────────────────────

    def get_officials_stats(self) -> dict:
        """Serve officials_stats.json for dashboard compatibility."""
        return self._read_json(self._data_dir() / "officials_stats.json", {})

    def get_morning_brief(self, date: str = "") -> dict:
        """Serve morning brief JSON. date='' returns latest."""
        if date:
            date_clean = date.replace("-", "").replace("/", "")
            path = self._data_dir() / f"morning_brief_{date_clean}.json"
            if path.exists():
                return self._read_json(path, {})
        return self._read_json(self._data_dir() / "morning_brief.json", {})

    def get_morning_config(self) -> dict:
        """Serve morning_brief_config.json."""
        default = {
            "categories": [
                {"name": "科技", "enabled": True},
                {"name": "AI", "enabled": True},
            ],
            "keywords": ["大模型", "LLM", "AI"],
            "custom_feeds": [],
            "feishu_webhook": "",
        }
        return self._read_json(self._data_dir() / "morning_brief_config.json", default)

    def refresh_morning_brief(self) -> dict:
        """Trigger morning brief refresh script."""
        try:
            scripts_dir = self._data_dir().parent / "scripts"
            result = subprocess.run(
                ["python3", str(scripts_dir / "refresh_morning_brief.py")],
                timeout=120,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return {"ok": True, "message": "早报已刷新"}
            return {"ok": False, "error": f"刷新失败: {result.stderr[:200]}"}
        except Exception as e:
            return {"ok": False, "error": f"刷新异常: {str(e)[:100]}"}

    def _check_agent_workspace(self, agent_id: str) -> bool:
        return (self._openclaw_home / f"workspace-{agent_id}").is_dir()
