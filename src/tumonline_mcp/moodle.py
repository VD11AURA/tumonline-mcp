"""TUM-Moodle über die Webservice-REST-API (Token: Moodle mobile web service)."""

from __future__ import annotations

import time
from datetime import datetime

from .client import setting
from .http import ServiceError, get_json

DEFAULT_BASE_URL = "https://www.moodle.tum.de"
CACHE_SECONDS = 300


class MoodleError(ServiceError):
    pass


def base_url() -> str:
    return setting("MOODLE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def token() -> str:
    return setting("MOODLE_TOKEN")


async def call(function: str, ttl: float = CACHE_SECONDS, **params):
    if not token():
        raise MoodleError(
            "MOODLE_TOKEN ist leer. Token holen: moodle.tum.de → Profil → Einstellungen → "
            "Sicherheitsschlüssel → 'Moodle mobile web service', dann `uv run moodle-token` ausführen."
        )
    params.update(wstoken=token(), wsfunction=function, moodlewsrestformat="json")
    data = await get_json(f"{base_url()}/webservice/rest/server.php", params, ttl=ttl, label=f"Moodle ({function})")
    if isinstance(data, dict) and data.get("exception"):
        raise MoodleError(f"Moodle-Fehler ({function}): {data.get('message') or data.get('errorcode')}")
    return data


def _ts(ts: int | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


async def site_info() -> dict:
    return await call("core_webservice_get_site_info", ttl=3600)


async def courses() -> list[dict]:
    info = await site_info()
    data = await call("core_enrol_get_users_courses", userid=info["userid"])
    return [
        {
            "kurs_id": c["id"],
            "name": c.get("fullname", ""),
            "kurzname": c.get("shortname", ""),
            "ende": _ts(c.get("enddate")),
            "url": f"{base_url()}/course/view.php?id={c['id']}",
        }
        for c in data
        if not c.get("hidden")
    ]


async def deadlines(days: int) -> list[dict]:
    now = int(time.time())
    data = await call(
        "core_calendar_get_action_events_by_timesort",
        timesortfrom=now, timesortto=now + days * 86400, limitnum=50,
    )
    out = []
    for e in data.get("events", []):
        action = e.get("action") or {}
        out.append({
            "titel": e.get("name", ""),
            "kurs": (e.get("course") or {}).get("fullname", ""),
            "faellig": _ts(e.get("timesort") or e.get("timestart")),
            "typ": e.get("modulename", ""),
            "erledigt_moeglich": bool(action.get("actionable")),
            "aktion": action.get("name", ""),
            "url": e.get("url") or action.get("url", ""),
        })
    return out


async def course_contents(course_id: int) -> list[dict]:
    data = await call("core_course_get_contents", courseid=course_id)
    sections = []
    for s in data:
        modules = []
        for m in s.get("modules", []):
            if not m.get("uservisible", True):
                continue
            mod = {"name": m.get("name", ""), "typ": m.get("modname", ""), "url": m.get("url", "")}
            files = [c.get("filename") for c in m.get("contents", []) if c.get("type") == "file"]
            if files:
                mod["dateien"] = files[:10]
            if added := max((c.get("timemodified") or 0 for c in m.get("contents", [])), default=0):
                mod["geaendert"] = _ts(added)
            modules.append(mod)
        if modules or s.get("summary"):
            sections.append({"abschnitt": s.get("name", ""), "inhalte": modules})
    return sections
