"""Moodle-Daten über die Browser-Sitzung (siehe moodle_session), im Format der Webservice-API.

TUM-Moodle sperrt viele Webservice-Funktionen auch für die Weboberfläche (core_course_get_contents,
mod_forum_get_forums_by_courses, gradereport_user_get_grade_items …). Dieses Modul baut die
Ergebnisse aus erlaubten Funktionen und normalen Moodle-Seiten nach, damit moodle.py und
moodle_sync.py unverändert damit arbeiten können.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urlparse

from . import moodle_session as ms
from .client import CONFIG_DIR

CACHE_SECONDS = 300
# Datei-Links von Ressourcen/Ordnern nur so oft neu auflösen: Jeder Aufruf der Ressourcenseite
# zählt in Moodle als "angesehen", das soll nicht bei jedem Abgleich passieren.
FILES_CACHE_SECONDS = 24 * 3600
FILES_CACHE_FILE = CONFIG_DIR / ".state" / "moodle_dateien_cache.json"
HEAD_CONCURRENCY = 6

_cache: dict[str, tuple[float, object]] = {}


async def _cached(key: str, ttl: float, factory):
    if (hit := _cache.get(key)) and hit[0] > time.monotonic():
        return hit[1]
    value = await factory()
    _cache[key] = (time.monotonic() + ttl, value)
    return value


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


# --- Benutzer und Kurse ---------------------------------------------------


async def site_info() -> dict:
    async def load():
        s = await ms.session()
        page, _ = await ms.get_page("/user/profile.php", {"id": s.userid})
        m = re.search(r"<title>(.*?)</title>", page, re.S)
        fullname = _text(m.group(1)).split(":")[0].split("|")[0].strip() if m else ""
        return {"userid": s.userid, "fullname": fullname}

    return await _cached("site_info", 3600, load)


async def raw_courses() -> list[dict]:
    async def load():
        data = await ms.ajax("core_course_get_enrolled_courses_by_timeline_classification",
                             {"classification": "all", "limit": 0, "offset": 0, "sort": "fullname"})
        return [{**c, "fullname": html.unescape(c.get("fullname", "")),
                 "shortname": html.unescape(c.get("shortname", ""))}
                for c in data.get("courses", []) if not c.get("hidden")]

    return await _cached("courses", CACHE_SECONDS, load)


# --- Kursinhalte ----------------------------------------------------------


async def course_state(course_id: int) -> dict:
    async def load():
        raw = await ms.ajax("core_courseformat_get_state", {"courseid": course_id})
        return json.loads(raw) if isinstance(raw, str) else raw

    return await _cached(f"state:{course_id}", CACHE_SECONDS, load)


def _files_cache() -> dict:
    try:
        return json.loads(FILES_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_files_cache(data: dict) -> None:
    FILES_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    FILES_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _file_entry(url: str, headers) -> dict:
    path = unquote(urlparse(url).path)
    # .../pluginfile.php/<ctx>/mod_folder/content/<rev>/<unterordner>/<datei>
    parts = path.split("/content/", 1)[1].split("/")[1:] if "/content/" in path else [path.rsplit("/", 1)[-1]]
    modified = 0
    if lm := headers.get("last-modified"):
        try:
            modified = int(parsedate_to_datetime(lm).timestamp())
        except (TypeError, ValueError):
            pass
    return {
        "type": "file",
        "filename": parts[-1],
        "filepath": "/" + "/".join(parts[:-1]) + ("/" if len(parts) > 1 else ""),
        "fileurl": url.split("?", 1)[0],
        "filesize": int(headers.get("content-length") or 0),
        "timemodified": modified,
    }


def folder_file_links(page: str) -> list[str]:
    links = re.findall(r'href="([^"]*/pluginfile\.php/\d+/mod_folder/content/[^"]+)"', page)
    return list(dict.fromkeys(html.unescape(link) for link in links))


async def _resolve_files(cm: dict) -> list[dict]:
    """Dateien einer Ressource bzw. eines Ordners (ruft die Moodle-Seite auf, daher gecacht)."""
    if cm["module"] == "resource":
        url, headers = await ms.head("/mod/resource/view.php", {"id": cm["id"], "redirect": 1})
        return [_file_entry(url, headers)] if "/pluginfile.php/" in url else []
    page, _ = await ms.get_page("/mod/folder/view.php", {"id": cm["id"]})
    sem = asyncio.Semaphore(HEAD_CONCURRENCY)

    async def one(link):
        async with sem:
            url, headers = await ms.head(link)
            return _file_entry(url, headers)

    return list(await asyncio.gather(*(one(link) for link in folder_file_links(page))))


async def course_contents(course_id: int, with_files: bool = True) -> list[dict]:
    """Wie core_course_get_contents: Abschnitte mit Modulen, bei Ressourcen/Ordnern mit Dateien."""
    state = await course_state(course_id)
    cms = {str(cm["id"]): cm for cm in state.get("cm", [])}
    cache, now, changed = _files_cache(), time.time(), False
    sections = []
    for sec in state.get("section", []):
        modules = []
        for cmid in sec.get("cmlist", []):
            cm = cms.get(str(cmid))
            if not cm:
                continue
            mod = {
                "id": int(cm["id"]),
                "name": html.unescape(cm.get("name", "")),
                "modname": cm.get("module", ""),
                "uservisible": cm.get("uservisible", True),
                "url": f"{ms.base_url()}/mod/{cm.get('module')}/view.php?id={cm['id']}",
                "contents": [],
            }
            if with_files and mod["uservisible"] and mod["modname"] in ("resource", "folder"):
                hit = cache.get(str(cm["id"]))
                if hit and now - hit["t"] < FILES_CACHE_SECONDS:
                    mod["contents"] = hit["files"]
                else:
                    try:
                        mod["contents"] = await _resolve_files(cm)
                    except ms.ServiceError:
                        mod["contents"] = []
                    cache[str(cm["id"])] = {"t": now, "files": mod["contents"]}
                    changed = True
            modules.append(mod)
        sections.append({"id": int(sec["id"]), "name": html.unescape(sec.get("title", "")).strip(),
                         "summary": "", "modules": modules})
    if changed:
        _save_files_cache(cache)
    return sections


# --- Ankündigungen --------------------------------------------------------

NEWS_NAME = re.compile(r"ankündig|ankuendig|announcement|nachrichten|news", re.IGNORECASE)


def news_forum_cmids(state: dict) -> list[str]:
    """Nachrichtenforen eines Kurses: per Name erkannt, sonst das erste Forum im Kopfabschnitt."""
    forums = [cm for cm in state.get("cm", []) if cm.get("module") == "forum" and cm.get("uservisible", True)]
    named = [str(cm["id"]) for cm in forums if NEWS_NAME.search(html.unescape(cm.get("name", "")))]
    if named:
        return named
    first = [str(cm["id"]) for cm in forums if int(cm.get("sectionnumber") or 0) == 0][:1]
    return first


def discussion_ids(page: str) -> list[tuple[int, bool]]:
    """(Diskussions-ID, angeheftet) in der Reihenfolge der Forenseite."""
    out, seen = [], set()
    for m in re.finditer(r'<tr[^>]*data-discussionid="(\d+)"[^>]*>', page):
        did = int(m.group(1))
        if did not in seen:
            seen.add(did)
            out.append((did, "pinned" in m.group(0)))
    if not out:  # andere Darstellung: nur Links
        for did in re.findall(r"/mod/forum/discuss\.php\?d=(\d+)", page):
            if int(did) not in seen:
                seen.add(int(did))
                out.append((int(did), False))
    return out


async def announcements(course_ids: list[int], days: int, limit_per_course: int = 10) -> list[dict]:
    from .moodle import _ts, html_to_text

    since = time.time() - days * 86400
    out = []
    for course_id in course_ids:
        state = await course_state(course_id)
        for cmid in news_forum_cmids(state):
            page, _ = await ms.get_page("/mod/forum/view.php", {"id": cmid})
            for did, pinned in discussion_ids(page)[:limit_per_course]:
                data = await _cached(f"disc:{did}", CACHE_SECONDS,
                                     lambda did=did: ms.ajax("mod_forum_get_discussion_posts",
                                                             {"discussionid": did, "sortby": "created",
                                                              "sortdirection": "ASC"}))
                posts = data.get("posts") or []
                if not posts:
                    continue
                first = posts[0]
                ts = max(p.get("timecreated") or 0 for p in posts)
                if ts < since and not pinned:
                    continue
                out.append({
                    "id": did,
                    "kurs_id": course_id,
                    "titel": first.get("subject", ""),
                    "von": (first.get("author") or {}).get("fullname", ""),
                    "datum": _ts(ts),
                    "zeitstempel": ts,
                    "angeheftet": pinned,
                    "text": html_to_text(first.get("message", "")),
                    "url": f"{ms.base_url()}/mod/forum/discuss.php?d={did}",
                })
    out.sort(key=lambda a: a["zeitstempel"], reverse=True)
    return out


# --- Bewertungen ----------------------------------------------------------


def _german_number(value: str) -> float | None:
    m = re.search(r"-?\d[\d.]*,?\d*", value.replace(" ", "").replace(" ", ""))
    if not m:
        return None
    num = m.group(0)
    num = num.replace(".", "").replace(",", ".") if "," in num else num
    try:
        return float(num)
    except ValueError:
        return None


def _cell(row: str, column: str) -> str:
    m = re.search(rf'<t[hd][^>]*class="[^"]*\bcolumn-{column}\b[^"]*"[^>]*>(.*?)</t[hd]>', row, re.S)
    return m.group(1) if m else ""


def parse_grade_report(page: str) -> list[dict]:
    """Zeilen des Bewertungsberichts (grade/report/user) im Format von gradereport_user_get_grade_items."""
    items = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        name_cell = _cell(row, "itemname")
        if not name_cell or not re.search(r'<td[^>]*class="[^"]*\bcolumn-grade\b', row):  # Kopfzeile: <th>
            continue
        header = re.search(r'<t[hd][^>]*class="([^"]*\bcolumn-itemname\b[^"]*)"', row).group(1)
        if re.search(r"\bcategory\b", header) and not re.search(r"\bitem\b", header):
            continue
        name = _text(name_cell)
        module = re.search(r"/mod/(\w+)/", name_cell)
        is_course = not module and re.search(r"kurs gesamt|course total|gesamt", name, re.IGNORECASE)
        is_category = "category" in header or re.search(r"kategorie gesamt|category total", name, re.IGNORECASE)
        grade = _text(_cell(row, "grade"))
        range_text = _text(_cell(row, "range"))
        bounds = re.split(r"[–-]", range_text) if range_text else []
        items.append({
            "itemname": name,
            "itemtype": "course" if is_course else ("category" if is_category else "mod"),
            "itemmodule": module.group(1) if module else "",
            "graderaw": _german_number(grade) if grade not in ("", "-") else None,
            "grademax": _german_number(bounds[-1]) if bounds else None,
            "gradeformatted": grade,
            "percentageformatted": _text(_cell(row, "percentage")),
        })
    return items


async def grade_items(course_id: int) -> dict:
    page, _ = await ms.get_page("/grade/report/user/index.php", {"id": course_id})
    return {"usergrades": [{"gradeitems": parse_grade_report(page)}]}
