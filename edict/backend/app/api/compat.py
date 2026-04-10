from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..services.legacy_compat_service import LegacyCompatService

router = APIRouter()


async def get_compat_service(
    db: AsyncSession = Depends(get_db),
) -> LegacyCompatService:
    return LegacyCompatService(db)


class SchedulerScanRequest(BaseModel):
    thresholdSec: int = 180


class ReviewActionRequest(BaseModel):
    taskId: str = ""
    action: str = ""
    comment: str = ""


class AdvanceStateRequest(BaseModel):
    taskId: str = ""
    comment: str = ""


class ArchiveTaskRequest(BaseModel):
    taskId: str = ""
    archived: bool = True
    archiveAllDone: bool = False


class SchedulerRetryRequest(BaseModel):
    taskId: str = ""
    reason: str = ""


class SchedulerEscalateRequest(BaseModel):
    taskId: str = ""
    reason: str = ""


class SchedulerRollbackRequest(BaseModel):
    taskId: str = ""
    reason: str = ""


class TaskActionRequest(BaseModel):
    taskId: str = ""
    action: str = ""
    reason: str = ""


class CreateTaskRequest(BaseModel):
    title: str = ""
    org: str = "中书省"
    targetDept: str = ""
    priority: str = "中"
    templateId: str = ""
    params: dict | None = None


class SetModelRequest(BaseModel):
    agentId: str = ""
    model: str = ""


class SetDispatchChannelRequest(BaseModel):
    channel: str = ""


class AddSkillRequest(BaseModel):
    agentId: str = ""
    skillName: str = ""
    description: str = ""
    trigger: str = ""


class AddRemoteSkillRequest(BaseModel):
    agentId: str = ""
    skillName: str = ""
    sourceUrl: str = ""
    description: str = ""


class UpdateRemoteSkillRequest(BaseModel):
    agentId: str = ""
    skillName: str = ""


class RemoveRemoteSkillRequest(BaseModel):
    agentId: str = ""
    skillName: str = ""


class AgentWakeRequest(BaseModel):
    agentId: str = ""
    message: str = ""


class MorningBriefRefreshRequest(BaseModel):
    date: str = ""


# ── Frontend-expected path aliases ───────────────────────────────────────────
# The frontend (api.ts) calls these paths which differ from the canonical ones.
# We add aliases so the frontend works without modification.


@router.post("/set-model")
async def legacy_set_model_alias(
    body: SetModelRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    agent_id = body.agentId.strip()
    model = body.model.strip()
    if not agent_id or not model:
        return {"ok": False, "error": "agentId and model required"}
    return svc.set_model(agent_id, model)


@router.post("/set-dispatch-channel")
async def legacy_set_dispatch_channel_alias(
    body: SetDispatchChannelRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    channel = body.channel.strip()
    if not channel:
        return {"ok": False, "error": "channel required"}
    return svc.set_dispatch_channel(channel)


@router.post("/add-skill")
async def legacy_add_skill_alias(
    body: AddSkillRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    agent_id = body.agentId.strip()
    skill_name = body.skillName.strip()
    if not agent_id or not skill_name:
        return {"ok": False, "error": "agentId and skillName required"}
    return svc.add_skill(agent_id, skill_name, body.description.strip(), body.trigger.strip())


@router.post("/add-remote-skill")
async def legacy_add_remote_skill_alias(
    body: AddRemoteSkillRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    agent_id = body.agentId.strip()
    skill_name = body.skillName.strip()
    source_url = body.sourceUrl.strip()
    if not agent_id or not skill_name:
        return {"ok": False, "error": "agentId and skillName required"}
    return svc.add_remote_skill(agent_id, skill_name, source_url, body.description.strip())


@router.get("/remote-skills-list")
async def legacy_remote_skills_list_alias(svc: LegacyCompatService = Depends(get_compat_service)):
    return svc.get_remote_skills_list()


@router.post("/update-remote-skill")
async def legacy_update_remote_skill_alias(
    body: UpdateRemoteSkillRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    agent_id = body.agentId.strip()
    skill_name = body.skillName.strip()
    if not agent_id or not skill_name:
        return {"ok": False, "error": "agentId and skillName required"}
    return svc.update_remote_skill(agent_id, skill_name)


@router.post("/remove-remote-skill")
async def legacy_remove_remote_skill_alias(
    body: RemoveRemoteSkillRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    agent_id = body.agentId.strip()
    skill_name = body.skillName.strip()
    if not agent_id or not skill_name:
        return {"ok": False, "error": "agentId and skillName required"}
    return svc.remove_remote_skill(agent_id, skill_name)


@router.post("/agent-wake")
async def legacy_agent_wake(
    body: AgentWakeRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    agent_id = body.agentId.strip()
    if not agent_id:
        return {"ok": False, "error": "agentId required"}
    return svc.wake_agent(agent_id, body.message.strip())


# ── File-based read endpoints ─────────────────────────────────────────────────


@router.get("/officials-stats")
async def legacy_officials_stats(svc: LegacyCompatService = Depends(get_compat_service)):
    return svc.get_officials_stats()


@router.get("/morning-brief")
async def legacy_morning_brief(
    date: str = Query(""),
    svc: LegacyCompatService = Depends(get_compat_service),
):
    return svc.get_morning_brief(date)


@router.get("/morning-config")
async def legacy_morning_config(svc: LegacyCompatService = Depends(get_compat_service)):
    return svc.get_morning_config()


@router.post("/morning-brief/refresh")
async def legacy_morning_brief_refresh(
    body: MorningBriefRefreshRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    return svc.refresh_morning_brief()


# ── Court Discuss ─────────────────────────────────────────────────────────────


@router.get("/court-discuss/list")
async def court_discuss_list():
    import sys
    from pathlib import Path
    dashboard_path = Path(__file__).resolve().parents[2] / "dashboard"
    if str(dashboard_path) not in sys.path:
        sys.path.insert(0, str(dashboard_path))
    from court_discuss import cd_list
    return cd_list()


@router.get("/court-discuss/officials")
async def court_discuss_officials():
    import sys
    from pathlib import Path
    dashboard_path = Path(__file__).resolve().parents[2] / "dashboard"
    if str(dashboard_path) not in sys.path:
        sys.path.insert(0, str(dashboard_path))
    from court_discuss import CD_PROFILES
    return {"ok": True, "officials": CD_PROFILES}


@router.get("/court-discuss/session/{session_id}")
async def court_discuss_session(session_id: str):
    import sys
    from pathlib import Path
    dashboard_path = Path(__file__).resolve().parents[2] / "dashboard"
    if str(dashboard_path) not in sys.path:
        sys.path.insert(0, str(dashboard_path))
    from court_discuss import cd_session
    return cd_session(session_id)


@router.get("/court-discuss/fate")
async def court_discuss_fate():
    import sys
    from pathlib import Path
    dashboard_path = Path(__file__).resolve().parents[2] / "dashboard"
    if str(dashboard_path) not in sys.path:
        sys.path.insert(0, str(dashboard_path))
    from court_discuss import cd_fate
    return cd_fate()


class CourtDiscussStartRequest(BaseModel):
    topic: str = ""
    officials: list[str] = []
    taskId: str = ""


class CourtDiscussAdvanceRequest(BaseModel):
    sessionId: str = ""
    userMessage: str = ""
    decree: str = ""


class CourtDiscussConcludeRequest(BaseModel):
    sessionId: str = ""


class CourtDiscussDestroyRequest(BaseModel):
    sessionId: str = ""


@router.post("/court-discuss/start")
async def court_discuss_start(body: CourtDiscussStartRequest):
    import sys
    from pathlib import Path
    dashboard_path = Path(__file__).resolve().parents[2] / "dashboard"
    if str(dashboard_path) not in sys.path:
        sys.path.insert(0, str(dashboard_path))
    from court_discuss import cd_create
    topic = body.topic.strip()
    officials = body.officials
    task_id = body.taskId.strip()
    if not topic:
        return {"ok": False, "error": "topic required"}
    if not officials or not isinstance(officials, list) or len(officials) < 2:
        return {"ok": False, "error": "至少选择2位官员"}
    from court_discuss import CD_PROFILES
    valid_ids = set(CD_PROFILES.keys())
    officials = [o for o in officials if o in valid_ids]
    if len(officials) < 2:
        return {"ok": False, "error": "至少需要2位有效官员"}
    return cd_create(topic, officials, task_id)


@router.post("/court-discuss/advance")
async def court_discuss_advance(body: CourtDiscussAdvanceRequest):
    import sys
    from pathlib import Path
    dashboard_path = Path(__file__).resolve().parents[2] / "dashboard"
    if str(dashboard_path) not in sys.path:
        sys.path.insert(0, str(dashboard_path))
    from court_discuss import cd_advance
    sid = body.sessionId.strip()
    user_msg = body.userMessage.strip() or None
    decree = body.decree.strip() or None
    if not sid:
        return {"ok": False, "error": "sessionId required"}
    return cd_advance(sid, user_msg, decree)


@router.post("/court-discuss/conclude")
async def court_discuss_conclude(body: CourtDiscussConcludeRequest):
    import sys
    from pathlib import Path
    dashboard_path = Path(__file__).resolve().parents[2] / "dashboard"
    if str(dashboard_path) not in sys.path:
        sys.path.insert(0, str(dashboard_path))
    from court_discuss import cd_conclude
    sid = body.sessionId.strip()
    if not sid:
        return {"ok": False, "error": "sessionId required"}
    return cd_conclude(sid)


@router.post("/court-discuss/destroy")
async def court_discuss_destroy(body: CourtDiscussDestroyRequest):
    import sys
    from pathlib import Path
    dashboard_path = Path(__file__).resolve().parents[2] / "dashboard"
    if str(dashboard_path) not in sys.path:
        sys.path.insert(0, str(dashboard_path))
    from court_discuss import cd_destroy
    sid = body.sessionId.strip()
    cd_destroy(sid)
    return {"ok": True}


# ── Skill / Agent Config Routes ──────────────────────────────────────────────


@router.get("/agent-config")
async def legacy_get_agent_config(svc: LegacyCompatService = Depends(get_compat_service)):
    return svc.get_agent_config()


@router.post("/agent-config/set-model")
async def legacy_set_model(
    body: SetModelRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    agent_id = body.agentId.strip()
    model = body.model.strip()
    if not agent_id or not model:
        return {"ok": False, "error": "agentId and model required"}
    return svc.set_model(agent_id, model)


@router.get("/model-change-log")
async def legacy_model_change_log(svc: LegacyCompatService = Depends(get_compat_service)):
    return svc.get_model_change_log()


@router.post("/dispatch-channel")
async def legacy_set_dispatch_channel(
    body: SetDispatchChannelRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    channel = body.channel.strip()
    if not channel:
        return {"ok": False, "error": "channel required"}
    return svc.set_dispatch_channel(channel)


@router.get("/remote-skills")
async def legacy_remote_skills(svc: LegacyCompatService = Depends(get_compat_service)):
    return svc.get_remote_skills_list()


@router.get("/skill-content/{agentId}/{skillName}")
async def legacy_skill_content(
    agentId: str,
    skillName: str,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    return svc.read_skill_content(agentId, skillName)


@router.post("/skills/add")
async def legacy_add_skill(
    body: AddSkillRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    agent_id = body.agentId.strip()
    skill_name = body.skillName.strip()
    if not agent_id or not skill_name:
        return {"ok": False, "error": "agentId and skillName required"}
    return svc.add_skill(agent_id, skill_name, body.description.strip(), body.trigger.strip())


@router.post("/skills/add-remote")
async def legacy_add_remote_skill(
    body: AddRemoteSkillRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    agent_id = body.agentId.strip()
    skill_name = body.skillName.strip()
    source_url = body.sourceUrl.strip()
    if not agent_id or not skill_name:
        return {"ok": False, "error": "agentId and skillName required"}
    return svc.add_remote_skill(agent_id, skill_name, source_url, body.description.strip())


@router.post("/skills/update-remote")
async def legacy_update_remote_skill(
    body: UpdateRemoteSkillRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    agent_id = body.agentId.strip()
    skill_name = body.skillName.strip()
    if not agent_id or not skill_name:
        return {"ok": False, "error": "agentId and skillName required"}
    return svc.update_remote_skill(agent_id, skill_name)


@router.post("/skills/remove-remote")
async def legacy_remove_remote_skill(
    body: RemoveRemoteSkillRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    agent_id = body.agentId.strip()
    skill_name = body.skillName.strip()
    if not agent_id or not skill_name:
        return {"ok": False, "error": "agentId and skillName required"}
    return svc.remove_remote_skill(agent_id, skill_name)


@router.get("/live-status")
async def legacy_live_status(svc: LegacyCompatService = Depends(get_compat_service)):
    return await svc.get_live_status()


@router.get("/agents-status")
async def legacy_agents_status(svc: LegacyCompatService = Depends(get_compat_service)):
    return await svc.get_agents_status()


@router.get("/task-activity/{legacy_id}")
async def legacy_task_activity(
    legacy_id: str,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    return await svc.get_task_activity(legacy_id)


@router.get("/scheduler-state/{legacy_id}")
async def legacy_scheduler_state(
    legacy_id: str,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    return await svc.get_scheduler_state(legacy_id)


@router.post("/scheduler-scan")
async def legacy_scheduler_scan(
    body: SchedulerScanRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    return await svc.scheduler_scan(body.thresholdSec)


@router.post("/review-action")
async def legacy_review_action(
    body: ReviewActionRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    task_id = body.taskId.strip()
    action = body.action.strip()
    comment = body.comment.strip()
    if not task_id or action not in {"approve", "reject"}:
        return {"ok": False, "error": "taskId and action(approve/reject) required"}
    return await svc.review_action(task_id, action, comment)


@router.post("/advance-state")
async def legacy_advance_state(
    body: AdvanceStateRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    task_id = body.taskId.strip()
    comment = body.comment.strip()
    if not task_id:
        return {"ok": False, "error": "taskId required"}
    return await svc.advance_state(task_id, comment)


@router.post("/archive-task")
async def legacy_archive_task(
    body: ArchiveTaskRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    task_id = body.taskId.strip()
    if body.archiveAllDone:
        return {"ok": True, "message": "批量归档已触发（当前 backend 批量归档待实现）"}
    if not task_id:
        return {"ok": False, "error": "taskId required"}
    return await svc.archive_task(task_id, body.archived)


@router.post("/scheduler-retry")
async def legacy_scheduler_retry(
    body: SchedulerRetryRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    task_id = body.taskId.strip()
    reason = body.reason.strip()
    if not task_id:
        return {"ok": False, "error": "taskId required"}
    return await svc.scheduler_retry(task_id, reason)


@router.post("/scheduler-escalate")
async def legacy_scheduler_escalate(
    body: SchedulerEscalateRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    task_id = body.taskId.strip()
    reason = body.reason.strip()
    if not task_id:
        return {"ok": False, "error": "taskId required"}
    return await svc.scheduler_escalate(task_id, reason)


@router.post("/scheduler-rollback")
async def legacy_scheduler_rollback(
    body: SchedulerRollbackRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    task_id = body.taskId.strip()
    reason = body.reason.strip()
    if not task_id:
        return {"ok": False, "error": "taskId required"}
    return await svc.scheduler_rollback(task_id, reason)


@router.post("/task-action")
async def legacy_task_action(
    body: TaskActionRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    task_id = body.taskId.strip()
    action = body.action.strip()
    reason = body.reason.strip()
    if not task_id or action not in {"stop", "cancel", "resume"}:
        return {"ok": False, "error": "taskId and action(stop/cancel/resume) required"}
    return await svc.task_action(task_id, action, reason)


@router.post("/create-task")
async def legacy_create_task(
    body: CreateTaskRequest,
    svc: LegacyCompatService = Depends(get_compat_service),
):
    return await svc.create_task(
        title=body.title,
        org=body.org,
        priority=body.priority,
        template_id=body.templateId,
        params=body.params,
        target_dept=body.targetDept,
    )

