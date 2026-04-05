"""OpenClaw 适配层。

把 Edict 的三省六部业务调用与当前 OpenClaw 运行时解耦：
- 环境检查
- session/runtime 读取
- agent 派发
- 能力探测
- 兼容旧 CLI / 新 CLI 行为差异
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import subprocess
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("edict.runtime_adapter")


@dataclass
class RuntimeCapabilities:
    openclaw_bin: str
    openclaw_version: str | None
    gateway_ok: bool
    skills_ok: bool
    claude_ok: bool
    gh_ok: bool


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return proc.returncode, out.strip()
    except Exception as e:
        return 1, str(e)


def get_openclaw_bin() -> str:
    return os.environ.get("OPENCLAW_BIN", "openclaw")


def get_runtime_capabilities() -> RuntimeCapabilities:
    bin_path = get_openclaw_bin()
    version = None
    gateway_ok = False
    skills_ok = False
    claude_ok = False
    gh_ok = False

    rc, out = _run([bin_path, "--version"], timeout=10)
    if rc == 0:
        version = out.splitlines()[0].strip() if out else None

    rc, out = _run([bin_path, "gateway", "status"], timeout=10)
    gateway_ok = rc == 0

    rc, out = _run([bin_path, "skills", "check"], timeout=20)
    skills_ok = rc == 0

    rc, _ = _run(["claude", "--version"], timeout=10)
    claude_ok = rc == 0

    rc, _ = _run(["gh", "--version"], timeout=10)
    gh_ok = rc == 0

    return RuntimeCapabilities(
        openclaw_bin=bin_path,
        openclaw_version=version,
        gateway_ok=gateway_ok,
        skills_ok=skills_ok,
        claude_ok=claude_ok,
        gh_ok=gh_ok,
    )


def ensure_openclaw_ready() -> dict[str, Any]:
    caps = get_runtime_capabilities()
    return {
        "openclaw_bin": caps.openclaw_bin,
        "openclaw_version": caps.openclaw_version,
        "gateway_ok": caps.gateway_ok,
        "skills_ok": caps.skills_ok,
        "claude_ok": caps.claude_ok,
        "gh_ok": caps.gh_ok,
        "ready": bool(caps.openclaw_version and caps.gateway_ok),
    }


def dispatch_agent(agent_id: str, prompt: str, *, timeout_sec: int = 300, deliver: bool = True) -> dict[str, Any]:
    """派发一个 agent 任务。

    先用当前 OpenClaw CLI 方式实现，后续如果版本变化只改这里。
    """
    bin_path = get_openclaw_bin()
    cmd = [bin_path, "agent", "--agent", agent_id, "-m", prompt, "--timeout", str(timeout_sec)]
    if deliver:
        cmd.append("--deliver")
    rc, out = _run(cmd, timeout=timeout_sec + 30)
    return {"returncode": rc, "output": out, "command": cmd}


def read_runtime_sessions(session_dir: str | None = None) -> list[dict[str, Any]]:
    """读取 OpenClaw runtime session 的本地投影。

    这里只做最小读取，避免把 session 结构写死在业务层。
    """
    base = pathlib.Path(session_dir or os.environ.get("OPENCLAW_SESSION_DIR", str(pathlib.Path.home() / ".openclaw" / "agents")))
    sessions = []
    if not base.exists():
        return sessions
    for p in sorted(base.rglob("*.jsonl")):
        sessions.append({"path": str(p), "type": "jsonl"})
    return sessions


def normalize_runtime_event(event: dict[str, Any]) -> dict[str, Any]:
    """把不同版本 OpenClaw runtime 事件标准化成统一结构。"""
    return {
        "timestamp": event.get("timestamp") or event.get("ts") or "",
        "role": event.get("role") or event.get("message", {}).get("role") or "",
        "content": event.get("content") or event.get("message", {}).get("content") or [],
        "raw": event,
    }
