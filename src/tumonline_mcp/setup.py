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


CONFIG_NAME = "claude_desktop_config.json"


def desktop_config_files() -> list[Path]:
    """Alle Orte, an denen Claude Desktop seine Konfiguration liest.

    Windows: Die Store-/MSIX-Version (heute der normale Installer) sieht %APPDATA% virtualisiert
    und liest aus Packages\\Claude_*\\LocalCache\\Roaming\\Claude. Was ein anderer Prozess nach
    %APPDATA%\\Claude schreibt, kommt dort nie an. Deshalb werden beide Orte bedient.
    """
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Application Support" / "Claude" / CONFIG_NAME]
    if os.name == "nt":
        files = []
        if local := os.environ.get("LOCALAPPDATA"):
            for package in sorted(Path(local, "Packages").glob("Claude_*")):
                files.append(package / "LocalCache" / "Roaming" / "Claude" / CONFIG_NAME)
        files.append(Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / CONFIG_NAME)
        return files
    return [Path.home() / ".config" / "Claude" / CONFIG_NAME]


def _is_msix(path: Path) -> bool:
    return "LocalCache" in path.parts


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


def _register_in(path: Path, command: str) -> str:
    config: dict = {}
    # utf-8-sig: Windows-Editoren schreiben gern ein BOM an den Anfang.
    if path.exists() and (text := path.read_text(encoding="utf-8-sig")).strip():
        try:
            config = json.loads(text)
        except json.JSONDecodeError as e:
            return f"{path} ist kein gültiges JSON ({e}), nicht verändert."
    servers = config.setdefault("mcpServers", {})
    if servers.get(SERVER_NAME, {}).get("command") == command:
        return "schon eingetragen."
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    servers[SERVER_NAME] = {"command": command, "args": []}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return "eingetragen (Claude Desktop ganz beenden, auch im Infobereich, und neu öffnen)."


def register_claude_desktop(command: str) -> str:
    # MSIX-Pfade zählen, sobald das Paket existiert (Ordner legt die App sonst erst beim Start an).
    targets = [p for p in desktop_config_files() if _is_msix(p) or p.parent.exists()]
    if not targets:
        return "Claude Desktop: nicht installiert, übersprungen."
    return "\n".join(f"Claude Desktop/Cowork ({p.parent}): {_register_in(p, command)}" for p in targets)


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
