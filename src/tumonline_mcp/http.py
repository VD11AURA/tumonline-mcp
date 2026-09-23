"""Gemeinsamer HTTP-Zugriff mit kurzem In-Memory-Cache und fehlerfreundlichen Meldungen."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

# httpx loggt sonst jede URL inkl. Tokens (pToken, wstoken) in die Claude-Logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

_cache: dict[str, tuple[float, bytes]] = {}


class ServiceError(ToolError):
    """Fehler, deren Text an das Modell weitergegeben wird."""


async def get_bytes(url: str, params: dict | None = None, *, ttl: float = 0, label: str = "") -> bytes:
    """GET mit optionalem Cache (ttl in Sekunden). Tokens landen nie in Fehlermeldungen."""
    params = params or {}
    key = f"{url}?{json.dumps(params, sort_keys=True, default=str)}"
    if ttl and (hit := _cache.get(key)) and hit[0] > time.monotonic():
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
            resp = await http.get(url, params=params)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise ServiceError(f"{label or url}: HTTP {e.response.status_code}") from e
    except httpx.HTTPError as e:
        raise ServiceError(f"{label or url} nicht erreichbar ({type(e).__name__})") from e
    if ttl:
        _cache[key] = (time.monotonic() + ttl, resp.content)
    return resp.content


async def get_json(url: str, params: dict | None = None, *, ttl: float = 0, label: str = "") -> Any:
    data = await get_bytes(url, params, ttl=ttl, label=label)
    try:
        return json.loads(data)
    except ValueError as e:
        raise ServiceError(f"{label or url}: ungültige JSON-Antwort") from e


def clear_cache() -> None:
    _cache.clear()


async def download(url: str, params: dict, dest, *, label: str = "Download") -> int:
    """Lädt eine Datei nach dest (erst .part, dann umbenennen). Gibt die Größe zurück."""
    tmp = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as http:
            async with http.stream("GET", url, params=params) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
                        size += len(chunk)
    except httpx.HTTPStatusError as e:
        tmp.unlink(missing_ok=True)
        raise ServiceError(f"{label}: HTTP {e.response.status_code}") from e
    except httpx.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise ServiceError(f"{label} fehlgeschlagen ({type(e).__name__})") from e
    tmp.replace(dest)
    return size
