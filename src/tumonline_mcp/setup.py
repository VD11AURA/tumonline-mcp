"""Einrichtung in einem Schritt: .env anlegen, Server in Claude Code und Claude Desktop
eintragen, TUMonline-Token anfordern bzw. prüfen.

Aufruf:  tumonline-setup [TUM-Kennung]
Mehrfach ausführbar; vorhandene Einträge und Tokens bleiben unverändert.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import client, token_setup

SERVER_NAME = "tumonline"


def server_command() -> str:
    """Pfad zum tumonline-mcp-Programm aus derselben Umgebung wie dieses Skript."""
    exe = Path(sys.executable).parent / ("tumonline-mcp.exe" if os.name == "nt" else "tumonline-mcp")
    if exe.exists():
        return str(exe)
    return shutil.which("tumonline-mcp") or str(exe)


def desktop_config_file() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def register_claude_code(command: str) -> str:
    claude = shutil.which("claude")
    if not claude:
        return "Claude Code: nicht gefunden, übersprungen."
    result = subprocess.run(
        [claude, "mcp", "add", "-s", "user", SERVER_NAME, "--", command],
        capture_output=True, text=True,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0:
        return "Claude Code: eingetragen (in Claude Code neu starten bzw. /mcp öffnen)."
    if "already exists" in output:
        return f"Claude Code: '{SERVER_NAME}' ist schon eingetragen, unverändert gelassen."
    return f"Claude Code: Eintragen fehlgeschlagen: {output}"


def register_claude_desktop(command: str) -> str:
    path = desktop_config_file()
    if not path.parent.exists():
        return "Claude Desktop: nicht installiert, übersprungen."
    config: dict = {}
    if path.exists() and path.read_text(encoding="utf-8").strip():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return f"Claude Desktop: {path} ist kein gültiges JSON ({e}), nicht verändert."
    servers = config.setdefault("mcpServers", {})
    if SERVER_NAME in servers:
        return f"Claude Desktop: '{SERVER_NAME}' ist schon eingetragen, unverändert gelassen."
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    servers[SERVER_NAME] = {"command": command, "args": []}
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return "Claude Desktop/Cowork: eingetragen (Claude Desktop ganz beenden und neu öffnen)."


async def token_step(kennung: str | None) -> str:
    if client.token():
        root = await client.call("isTokenConfirmed")
        if (root.text or "").strip().lower() == "true":
            return "TUMonline-Token: aktiviert, alles bereit."
        return (
            "TUMonline-Token: angefordert, aber noch NICHT aktiviert.\n"
            "  campus.tum.de → Visitenkarte → Token-Management → Token "
            f"'{token_setup.TOKEN_NAME}' aktivieren\n"
            "  und Rechte für Kalender, Lehrveranstaltungen und Noten freigeben.\n"
            "  Danach prüfen mit:  tumonline-token --check"
        )
    if not kennung and sys.stdin.isatty():
        kennung = input("TUM-Kennung (z. B. ab12cde, leer = später): ").strip()
    if not kennung:
        return "TUMonline-Token: fehlt noch. Anfordern mit:  tumonline-token <TUM-Kennung>"
    if await token_setup._request(kennung) != 0:
        return "TUMonline-Token: nicht angefordert (siehe oben)."
    return "TUMonline-Token: angefordert, jetzt wie oben beschrieben in TUMonline aktivieren."


def main() -> None:
    p = argparse.ArgumentParser(prog="tumonline-setup", description=__doc__.splitlines()[0])
    p.add_argument("kennung", nargs="?", help="TUM-Kennung, z. B. ab12cde (fordert einen Token an)")
    args = p.parse_args()

    token_setup.ensure_env_file()
    command = server_command()
    print(f"Konfiguration: {client.ENV_FILE}")
    print(f"Server: {command}\n")
    print(register_claude_code(command))
    print(register_claude_desktop(command))
    print()
    try:
        print(asyncio.run(token_step(args.kennung)))
    except client.TUMonlineError as e:
        print(f"TUMonline: {e}")
        sys.exit(1)
    print("\nOptional Moodle: in einem Terminal  moodle-login  ausführen (TUM-Kennung + Passwort, verdeckt).")


if __name__ == "__main__":
    main()
