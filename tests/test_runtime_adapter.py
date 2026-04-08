import importlib.util
import sys
from pathlib import Path


def _load_runtime_adapter():
    root = Path(__file__).resolve().parents[1]
    if str(root / 'scripts') not in sys.path:
        sys.path.insert(0, str(root / 'scripts'))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    script_path = root / "scripts" / "runtime_adapter.py"
    spec = importlib.util.spec_from_file_location("runtime_adapter", script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_discover_openclaw_config_path_prefers_env(tmp_path, monkeypatch):
    runtime_adapter = _load_runtime_adapter()
    cfg = tmp_path / "custom-openclaw.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(cfg))
    assert runtime_adapter.discover_openclaw_config_path() == cfg


def test_build_agent_command_uses_current_openclaw_cli_shape():
    runtime_adapter = _load_runtime_adapter()
    cmd = runtime_adapter._build_agent_command(
        "openclaw",
        "taizi",
        "hello",
        timeout_sec=60,
        deliver=False,
    )
    assert cmd[:6] == ["openclaw", "agent", "--agent", "taizi", "--message", "hello"]
    assert "--timeout" in cmd
    assert "--json" in cmd
    assert "--local" in cmd
    assert "--deliver" not in cmd


def test_build_agent_command_uses_deliver_flag_when_requested():
    runtime_adapter = _load_runtime_adapter()
    cmd = runtime_adapter._build_agent_command(
        "openclaw",
        "taizi",
        "hello",
        timeout_sec=60,
        deliver=True,
    )
    assert "--deliver" in cmd
    assert "--local" not in cmd
