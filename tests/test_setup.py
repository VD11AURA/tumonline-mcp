"""Eintragen in Claude Desktop: Store-/MSIX-Pfad unter Windows und Configs mit BOM."""

import json
import os

import pytest

from tumonline_mcp import setup

CMD = r"C:\tools\tumonline-mcp.exe"


@pytest.fixture
def windows_dirs(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows-Pfade")
    local, roaming = tmp_path / "Local", tmp_path / "Roaming"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    return local, roaming


def test_msix_config_is_used_even_before_first_start(windows_dirs):
    local, roaming = windows_dirs
    (local / "Packages" / "Claude_abc123").mkdir(parents=True)
    out = setup.register_claude_desktop(CMD)
    msix = local / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming" / "Claude" / setup.CONFIG_NAME
    assert json.loads(msix.read_text(encoding="utf-8"))["mcpServers"]["tumonline"]["command"] == CMD
    assert "eingetragen" in out
    assert not (roaming / "Claude").exists()  # klassischer Ordner nur, wenn es ihn schon gibt


def test_bom_config_is_extended_and_existing_servers_kept(windows_dirs):
    _, roaming = windows_dirs
    cfg = roaming / "Claude" / setup.CONFIG_NAME
    cfg.parent.mkdir(parents=True)
    cfg.write_text("\ufeff" + json.dumps({"mcpServers": {"andere": {"command": "x"}}, "preferences": {"a": 1}}),
                   encoding="utf-8")
    setup.register_claude_desktop(CMD)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert set(data["mcpServers"]) == {"andere", "tumonline"} and data["preferences"] == {"a": 1}
    assert cfg.with_suffix(".json.bak").exists()
    assert "schon eingetragen" in setup.register_claude_desktop(CMD)


def test_no_claude_desktop(windows_dirs):
    assert "nicht installiert" in setup.register_claude_desktop(CMD)
