"""Minimaler Client für die TUMonline/CAMPUSonline-Webservices (wbservicesbasic.*)."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

from dotenv import dotenv_values

from .http import ServiceError, get_bytes

PROJECT_DIR = Path(__file__).resolve().parents[2]


def _config_dir() -> Path:
    # Git-Checkout: Konfiguration im Projektordner; installiert (uv tool install):
    # ~/.config/tumonline-mcp. TUMONLINE_MCP_HOME überschreibt beides.
    if explicit := os.getenv("TUMONLINE_MCP_HOME"):
        return Path(explicit).expanduser()
    if (PROJECT_DIR / "pyproject.toml").exists():
        return PROJECT_DIR
    return Path.home() / ".config" / "tumonline-mcp"


CONFIG_DIR = _config_dir()
ENV_FILE = CONFIG_DIR / ".env"
DEFAULT_BASE_URL = "https://campus.tum.de/tumonline"
CACHE_SECONDS = 300


class TUMonlineError(ServiceError):
    pass


def setting(key: str, default: str = "") -> str:
    # .env bei jedem Aufruf frisch lesen (aus CONFIG_DIR, nicht relativ zum cwd), damit
    # ein neuer Token ohne Neustart des Servers greift. Umgebungsvariable als Fallback.
    values = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
    return (values.get(key) or os.getenv(key) or default).strip()


def base_url() -> str:
    return setting("TUMONLINE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def token() -> str:
    return setting("TUMONLINE_TOKEN")


def _records(root: ET.Element) -> list[dict[str, str]]:
    """Wandelt <rowset><row>…</row></rowset> bzw. <events><event>… in Dicts um."""
    out = []
    for child in root:
        if len(child):
            out.append({el.tag: (el.text or "").strip() for el in child if not len(el)})
    return out


def parse(endpoint: str, content: bytes) -> ET.Element:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise TUMonlineError(f"Ungültige Antwort von TUMonline ({endpoint}): {e}") from e
    if root.tag == "error":
        msg = (root.findtext("message") or "unbekannter Fehler").strip()
        raise TUMonlineError(f"TUMonline-Fehler ({endpoint}): {msg}")
    return root


async def call(endpoint: str, *, needs_token: bool = True, ttl: float = 0, **params) -> ET.Element:
    if needs_token:
        if not token():
            raise TUMonlineError(
                f"TUMONLINE_TOKEN ist leer. Token in {ENV_FILE} eintragen "
                "(oder `tumonline-token <TUM-Kennung>` ausführen)."
            )
        params["pToken"] = token()
    url = f"{base_url()}/wbservicesbasic.{endpoint}"
    try:
        content = await get_bytes(url, params, ttl=ttl, label=f"TUMonline ({endpoint})")
    except ServiceError as e:
        raise TUMonlineError(str(e)) from e
    return parse(endpoint, content)


async def records(endpoint: str, **params) -> list[dict[str, str]]:
    return _records(await call(endpoint, ttl=CACHE_SECONDS, **params))
