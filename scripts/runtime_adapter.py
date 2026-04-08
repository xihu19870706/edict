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
import shlex
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
    has_agent_subcommand: bool
    openclaw_config_path: str | None


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return proc.returncode, out.strip()
    except Exception as e:
        return 1, str(e)


def get_openclaw_bin() -> str:
    return os.environ.get("OPENCLAW_BIN", "openclaw")


def discover_openclaw_config_path() -> pathlib.Path | None:
    candidates = [
        pathlib.Path(os.environ["OPENCLAW_CONFIG_PATH"])
        if os.environ.get("OPENCLAW_CONFIG_PATH")
        else None,
        pathlib.Path.home() / ".openclaw" / "openclaw.json",
        pathlib.Path.home() / ".openclaw" / "config.json",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def _cli_supports_agent_subcommand(bin_path: str) -> bool:
    rc, out = _run([bin_path, "help"], timeout=10)
    text = out.lower()
    if rc == 0 and ("\n  agent\n" in text or " agent " in text or "agent" in text):
        return True
    rc, out = _run([bin_path, "agent", "--help"], timeout=10)
    return rc == 0 and "usage" in out.lower()


def get_runtime_capabilities() -> RuntimeCapabilities:
    bin_path = get_openclaw_bin()
    version = None
    gateway_ok = False
    skills_ok = False
    claude_ok = False
    gh_ok = False
    has_agent_subcommand = False
    config_path = discover_openclaw_config_path()

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

    has_agent_subcommand = _cli_supports_agent_subcommand(bin_path)

    return RuntimeCapabilities(
        openclaw_bin=bin_path,
        openclaw_version=version,
        gateway_ok=gateway_ok,
        skills_ok=skills_ok,
        claude_ok=claude_ok,
        gh_ok=gh_ok,
        has_agent_subcommand=has_agent_subcommand,
        openclaw_config_path=str(config_path) if config_path else None,
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
        "has_agent_subcommand": caps.has_agent_subcommand,
        "openclaw_config_path": caps.openclaw_config_path,
        "ready": bool(caps.openclaw_version and caps.gateway_ok and caps.has_agent_subcommand),
    }


def _build_agent_command(
    bin_path: str,
    agent_id: str,
    prompt: str,
    *,
    timeout_sec: int,
    deliver: bool,
) -> list[str]:
    cmd = [bin_path, "agent", "--agent", agent_id, "-m", prompt, "--timeout", str(timeout_sec)]
    if deliver:
        cmd.append("--deliver")
    return cmd


def dispatch_agent(agent_id: str, prompt: str, *, timeout_sec: int = 300, deliver: bool = True) -> dict[str, Any]:
    """派发一个 agent 任务。

    当前实现仍保持 Edict 的“通过 OpenClaw CLI 派发”架构不变，
    但把版本探测、命令构造、错误提示统一收敛在这里，避免业务层
    写死当前 OpenClaw 的具体命令细节。
    """
    caps = get_runtime_capabilities()
    if not caps.openclaw_version:
        return {
            "returncode": 127,
            "output": "openclaw CLI not available",
            "command": [],
        }
    if not caps.has_agent_subcommand:
        return {
            "returncode": 2,
            "output": (
                "Current OpenClaw CLI does not expose a compatible `agent` subcommand. "
                f"openclaw_bin={caps.openclaw_bin}; version={caps.openclaw_version}; "
                f"config={caps.openclaw_config_path}; has_agent_subcommand={caps.has_agent_subcommand}. "
                "Please adapt runtime_adapter.dispatch_agent() to the installed OpenClaw version."
            ),
            "command": [caps.openclaw_bin, "agent"],
        }

    cmd = _build_agent_command(
        caps.openclaw_bin,
        agent_id,
        prompt,
        timeout_sec=timeout_sec,
        deliver=deliver,
    )
    rc, out = _run(cmd, timeout=timeout_sec + 30)
    return {
        "returncode": rc,
        "output": out,
        "command": cmd,
        "command_preview": " ".join(shlex.quote(part) for part in cmd[:6]) + (" ..." if len(cmd) > 6 else ""),
    }


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
