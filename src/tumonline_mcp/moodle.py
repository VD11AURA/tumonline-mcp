"""TUM-Moodle: Webservice-REST-API mit Token, sonst über die TUM-Login-Sitzung.

Mit MOODLE_TOKEN (Moodle mobile web service) wird die REST-API genutzt. TUM-Moodle vergibt diese
Tokens nicht mehr; dann läuft alles über moodle_session/moodle_web (Anmeldung: `moodle-login`).
"""

from __future__ import annotations

import html
import re
import time
from datetime import datetime

from . import moodle_session, moodle_web
from .client import setting
from .http import ServiceError, download, get_json

DEFAULT_BASE_URL = "https://www.moodle.tum.de"
CACHE_SECONDS = 300


class MoodleError(ServiceError):
    pass


def base_url() -> str:
    return setting("MOODLE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def token() -> str:
    return setting("MOODLE_TOKEN")


def uses_session() -> bool:
    """Kein Token, aber TUM-Login eingerichtet: alles über die Browser-Sitzung."""
    return not token() and moodle_session.is_configured()


def configured() -> bool:
    return bool(token()) or moodle_session.is_configured()


def _flatten(params: dict) -> dict:
    """Moodle erwartet Listen als courseids[0]=1&courseids[1]=2."""
    out = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            out.update({f"{key}[{i}]": v for i, v in enumerate(value)})
        else:
            out[key] = value
    return out


def html_to_text(value: str, limit: int = 2000) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", value or "", flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


async def call(function: str, ttl: float = CACHE_SECONDS, **params):
    if not token():
        if moodle_session.is_configured():
            return await moodle_session.ajax(function, params)
        raise MoodleError(
            "Moodle ist nicht eingerichtet. In einem Terminal `moodle-login` ausführen "
            "(TUM-Kennung und Passwort) – oder, wo es noch Mobile-Tokens gibt, `moodle-token`."
        )
    params = _flatten(params)
    params.update(wstoken=token(), wsfunction=function, moodlewsrestformat="json")
    data = await get_json(f"{base_url()}/webservice/rest/server.php", params, ttl=ttl, label=f"Moodle ({function})")
    if isinstance(data, dict) and data.get("exception"):
        raise MoodleError(f"Moodle-Fehler ({function}): {data.get('message') or data.get('errorcode')}")
    return data


def _ts(ts: int | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


async def site_info() -> dict:
    if uses_session():
        return await moodle_web.site_info()
    return await call("core_webservice_get_site_info", ttl=3600)


async def raw_courses() -> list[dict]:
    if uses_session():
        return await moodle_web.raw_courses()
    info = await site_info()
    return [c for c in await call("core_enrol_get_users_courses", userid=info["userid"]) if not c.get("hidden")]


def is_current(course: dict, now: float | None = None) -> bool:
    """Kurs läuft gerade oder beginnt in den nächsten 30 Tagen."""
    now = now or time.time()
    start, end = course.get("startdate") or 0, course.get("enddate") or 0
    return start <= now + 30 * 86400 and (not end or end >= now)


async def courses(nur_aktuelle: bool = False) -> list[dict]:
    return [
        {
            "kurs_id": c["id"],
            "name": c.get("fullname", ""),
            "kurzname": c.get("shortname", ""),
            "aktuell": is_current(c),
            "ende": _ts(c.get("enddate")),
            "url": f"{base_url()}/course/view.php?id={c['id']}",
        }
        for c in await raw_courses()
        if not nur_aktuelle or is_current(c)
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


async def contents(course_id: int, with_files: bool = True) -> list[dict]:
    """Abschnitte mit Modulen im Format von core_course_get_contents."""
    if uses_session():
        return await moodle_web.course_contents(course_id, with_files=with_files)
    return await call("core_course_get_contents", courseid=course_id)


async def course_contents(course_id: int) -> list[dict]:
    # Ohne Dateiauflösung über die Sitzung: die markiert in Moodle jede Ressource als "angesehen".
    data = await contents(course_id, with_files=False)
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


async def announcements(course_ids: list[int], days: int, limit_per_course: int = 10) -> list[dict]:
    """Beiträge der Nachrichtenforen (Ankündigungen der Lehrenden), neueste zuerst."""
    if not course_ids:
        return []
    if uses_session():
        return await moodle_web.announcements(course_ids, days, limit_per_course)
    forums = await call("mod_forum_get_forums_by_courses", courseids=course_ids)
    since = time.time() - days * 86400
    out = []
    for forum in forums:
        if forum.get("type") != "news":
            continue
        data = await call("mod_forum_get_forum_discussions", forumid=forum["id"], sortorder=-1,
                          page=0, perpage=limit_per_course)
        for d in data.get("discussions", []):
            ts = d.get("timemodified") or d.get("modified") or d.get("created") or 0
            if ts < since:
                continue
            out.append({
                "id": d.get("discussion") or d.get("id"),
                "kurs_id": forum.get("course"),
                "titel": d.get("subject") or d.get("name", ""),
                "von": d.get("userfullname", ""),
                "datum": _ts(ts),
                "zeitstempel": ts,
                "angeheftet": bool(d.get("pinned")),
                "text": html_to_text(d.get("message", "")),
                "url": f"{base_url()}/mod/forum/discuss.php?d={d.get('discussion') or d.get('id')}",
            })
    out.sort(key=lambda a: a["zeitstempel"], reverse=True)
    return out


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def grades(course_id: int) -> dict:
    """Bewertungen eines Kurses (z. B. Übungspunkte) mit Summe der erreichten Punkte."""
    if uses_session():
        data = await moodle_web.grade_items(course_id)
    else:
        info = await site_info()
        data = await call("gradereport_user_get_grade_items", courseid=course_id, userid=info["userid"])
    users = data.get("usergrades") or []
    items, total = [], None
    for g in (users[0].get("gradeitems", []) if users else []):
        entry = {
            "name": g.get("itemname") or "",
            "typ": g.get("itemmodule") or g.get("itemtype", ""),
            "punkte": _num(g.get("graderaw")),
            "max": _num(g.get("grademax")),
            "anzeige": html_to_text(g.get("gradeformatted", ""), 100),
            "prozent": html_to_text(g.get("percentageformatted", ""), 20),
        }
        if g.get("itemtype") == "course":
            total = entry
        elif g.get("itemtype") != "category":
            items.append(entry)
    graded = [i for i in items if i["punkte"] is not None]
    out = {
        "kurs_id": course_id,
        "bewertungen": items,
        "bewertet": len(graded),
        "summe_punkte": sum(i["punkte"] for i in graded),
        "summe_max_bewertet": sum(i["max"] or 0 for i in graded),
        "summe_max_alle": sum(i["max"] or 0 for i in items),
    }
    if total:
        out["kursgesamt"] = total
    return out


async def file_download(fileurl: str, dest) -> int:
    if uses_session():
        return await moodle_session.download(fileurl, dest)
    return await download(fileurl, {"token": token()}, dest, label="Moodle-Datei")
