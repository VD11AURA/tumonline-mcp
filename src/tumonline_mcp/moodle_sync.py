"""Lädt Moodle-Dateien der aktuellen Kurse in die lokale Studium-Ordnerstruktur.

Zielordner: <STUDIUM_DIR>/<Semesterordner>/<Fach>/<Kategorie>/<Datei>
- Semesterordner: der Ordner, dessen Name auf das aktuelle Semester endet (z. B. 01_WS2627).
- Fach: vorhandener Ordner, der zum Kursnamen passt (Analysis1, LA1, ExPhysik1 …), sonst neu.
- Kategorie: Übungen / Skript / Altklausuren / Moodle-Sonstiges, anhand von Abschnitt, Modul und Dateiname.

Zuordnungen werden in .state/moodle_ordner.json gespeichert und können dort angepasst werden.
Lokal veränderte Dateien (z. B. mit Notizen) werden nie überschrieben; neue Moodle-Versionen
landen dann daneben als "Name (Moodle JJJJ-MM-TT).pdf".
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from . import moodle
from .client import CONFIG_DIR, setting
from .http import ServiceError

STATE_DIR = CONFIG_DIR / ".state"
MAPPING_FILE = STATE_DIR / "moodle_ordner.json"
SYNC_STATE_FILE = STATE_DIR / "moodle_sync.json"

CATEGORIES = [
    ("Altklausuren", r"altklausur|probeklausur|klausur|exam|pr[üu]fung"),
    ("Übungen", r"[üu]bung|uebung|blatt|sheet|exercise|aufgabe|hausaufgabe|homework|tutor|l[öo]sung|loesung|solution|zentral"),
    ("Skript", r"skript|script|vorlesung|lecture|folie|slides|kapitel|chapter|notes|mitschrieb|tafel"),
]
OTHER = "Moodle-Sonstiges"
# Organisatorisches nie über den Abschnittsnamen einsortieren (liegt oft im Abschnitt "Vorlesung")
ORGA = r"organisat|\borga\b|syllabus|termine|zeitplan|schedule|anmeldung|allgemein"

# Kurzformen in Ordnernamen → ausgeschriebene Form in Moodle-Kursnamen
ALIASES = {
    "la": ["lineare algebra"],
    "exphysik": ["experimentalphysik", "experimental physics"],
    "exphys": ["experimentalphysik"],
    "ana": ["analysis"],
    "info": ["informatik", "einführung in die informatik"],
    "theo": ["theoretische physik"],
    "numerik": ["numerische mathematik", "numerik"],
    "stochastik": ["stochastik", "wahrscheinlichkeitstheorie"],
}
SKIP_DIRS = {"mitschriften"}


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def safe_name(value: str, limit: int = 120) -> str:
    value = nfc(value).replace("/", "-").replace(":", "-")
    value = re.sub(r"[\x00-\x1f\\*?\"<>|]", "", value).strip(" .")
    return value[:limit] or "unbenannt"


def studium_dir() -> Path:
    return Path(setting("STUDIUM_DIR", str(Path.home() / "Studium"))).expanduser()


def semester_code(today: date | None = None) -> str:
    """WS2627 von September bis März, sonst SS27 (passend zu 01_WS2627 / 02_SS27)."""
    d = today or date.today()
    if d.month >= 9:
        return f"WS{d.year % 100:02d}{(d.year + 1) % 100:02d}"
    if d.month <= 3:
        return f"WS{(d.year - 1) % 100:02d}{d.year % 100:02d}"
    return f"SS{d.year % 100:02d}"


def semester_dir(today: date | None = None) -> Path:
    if explicit := setting("MOODLE_SYNC_DIR"):
        return Path(explicit).expanduser()
    base, code = studium_dir(), semester_code(today)
    for child in sorted(base.iterdir()) if base.exists() else []:
        if child.is_dir() and nfc(child.name).upper().endswith(code):
            return child
    return base / code


def _pattern_for_folder(folder: str) -> re.Pattern | None:
    """'LA1' → matcht 'Lineare Algebra 1', 'LA 1', 'LA1'; 'Analysis1' → 'Analysis 1'."""
    m = re.fullmatch(r"([A-Za-zÄÖÜäöüß]+?)[\s_-]*(\d*)", nfc(folder))
    if not m:
        return None
    word, num = m.group(1).lower(), m.group(2)
    variants = [word, *ALIASES.get(word, [])]
    alts = "|".join(re.escape(v).replace(r"\ ", r"\s+") for v in variants)
    suffix = rf"\s*(?:{num}|{'I' * int(num)})\b" if num and int(num) <= 3 else (rf"\s*{num}\b" if num else r"\b")
    return re.compile(rf"(?<![A-Za-zÄÖÜäöü])(?:{alts}){suffix}", re.IGNORECASE)


def guess_folder(course: dict, sem_dir: Path) -> Path:
    text = nfc(f"{course.get('fullname', '')} | {course.get('shortname', '')}")
    if sem_dir.exists():
        for child in sorted(sem_dir.iterdir()):
            if child.is_dir() and not child.name.startswith(".") and child.name.lower() not in SKIP_DIRS:
                pat = _pattern_for_folder(child.name)
                if pat and pat.search(text):
                    return child
    name = re.sub(r"\s*\((?:WS|SS|WiSe|SoSe)[^)]*\)|\s*(?:WS|SS|WiSe|SoSe)\s*\d{2,4}(?:/\d{2,4})?", "", course.get("fullname", ""))
    return sem_dir / safe_name(name, 60)


def category(section: str, module: str, filename: str) -> str:
    for name, pattern in CATEGORIES:
        if re.search(pattern, f"{module} {filename}", re.IGNORECASE):
            return name
    if re.search(ORGA, f"{module} {filename}", re.IGNORECASE):
        return OTHER
    for name, pattern in CATEGORIES:  # Abschnittsname nur als schwächerer Hinweis
        if re.search(pattern, section, re.IGNORECASE):
            return name
    return OTHER


def existing_child(parent: Path, name: str) -> Path:
    """Nutzt vorhandene Ordner unabhängig von Unicode-Normalisierung/Groß-Kleinschreibung (Übungen)."""
    if parent.exists():
        for child in parent.iterdir():
            if nfc(child.name).lower() == nfc(name).lower():
                return child
    return parent / name


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)


def plan_course_files(contents: list[dict], course_dir: Path) -> list[dict]:
    """Alle herunterladbaren Dateien eines Kurses mit Zielpfad."""
    planned, taken = [], set()
    for section in contents:
        sec_name = section.get("name", "")
        for mod in section.get("modules", []):
            if not mod.get("uservisible", True) or mod.get("modname") not in ("resource", "folder"):
                continue
            for c in mod.get("contents", []):
                if c.get("type") != "file" or not c.get("fileurl"):
                    continue
                filename = safe_name(c.get("filename", ""))
                cat_dir = existing_child(course_dir, category(sec_name, mod.get("name", ""), filename))
                if mod.get("modname") == "folder":
                    sub = [safe_name(p) for p in (c.get("filepath") or "/").strip("/").split("/") if p]
                    target = cat_dir.joinpath(safe_name(mod.get("name", ""), 60), *sub, filename)
                else:
                    target = cat_dir / filename
                if target in taken:  # gleicher Dateiname aus verschiedenen Modulen
                    target = target.with_name(f"{safe_name(mod.get('name', ''), 60)} - {filename}")
                taken.add(target)
                planned.append({
                    "url": c["fileurl"],
                    "ziel": target,
                    "groesse": c.get("filesize") or 0,
                    "geaendert": c.get("timemodified") or 0,
                    "modul": mod.get("name", ""),
                })
    return planned


async def sync(dry_run: bool = False, max_mb: float | None = None) -> dict:
    """Gleicht die Dateien aller aktuellen Moodle-Kurse ab. Gibt eine Zusammenfassung zurück."""
    max_bytes = (max_mb if max_mb is not None else float(setting("MOODLE_SYNC_MAX_MB", "100"))) * 1024 * 1024
    sem_dir = semester_dir()
    mapping = _load(MAPPING_FILE)
    state = _load(SYNC_STATE_FILE)
    result = {"semesterordner": str(sem_dir), "probelauf": dry_run, "kurse": [], "neu": [],
              "aktualisiert": [], "neben_lokaler_aenderung": [], "uebersprungen_zu_gross": [], "fehler": []}

    courses = [c for c in await moodle.raw_courses() if moodle.is_current(c)]
    for course in courses:
        key = str(course["id"])
        if key not in mapping:
            mapping[key] = {"name": course.get("fullname", ""), "ordner": str(guess_folder(course, sem_dir))}
        entry = mapping[key]
        result["kurse"].append({"kurs": entry["name"], "ordner": entry["ordner"], "aktiv": entry.get("aktiv", True)})
        if entry.get("aktiv", True) is False:
            continue
        try:
            contents = await moodle.call("core_course_get_contents", courseid=course["id"])
        except ServiceError as e:
            result["fehler"].append(f"{entry['name']}: {e}")
            continue

        for f in plan_course_files(contents, Path(entry["ordner"])):
            known = state.get(f["url"])
            target = Path(known["pfad"]) if known else f["ziel"]
            if known and known.get("geaendert", 0) >= f["geaendert"] and target.exists():
                continue  # unverändert
            if f["groesse"] > max_bytes:
                result["uebersprungen_zu_gross"].append(f"{target.name} ({f['groesse'] // 2**20} MB)")
                continue
            kind = "aktualisiert" if known else "neu"
            if target.exists():
                st = target.stat()
                locally_changed = not known or st.st_size != known.get("groesse") or int(st.st_mtime) != known.get("mtime")
                if locally_changed:
                    stamp = datetime.fromtimestamp(f["geaendert"] or datetime.now().timestamp()).strftime("%Y-%m-%d")
                    target = target.with_name(f"{target.stem} (Moodle {stamp}){target.suffix}")
                    kind = "neben_lokaler_aenderung"
                    if target.exists():
                        continue
            result[kind].append(str(target.relative_to(sem_dir)) if target.is_relative_to(sem_dir) else str(target))
            if dry_run:
                continue
            try:
                size = await moodle.file_download(f["url"], target)
            except ServiceError as e:
                result["fehler"].append(f"{target.name}: {e}")
                result[kind].pop()
                continue
            state[f["url"]] = {"pfad": str(target), "geaendert": f["geaendert"], "groesse": size,
                               "mtime": int(target.stat().st_mtime)}
            _save(SYNC_STATE_FILE, state)  # nach jeder Datei, damit Abbrüche nichts doppelt laden

    _save(MAPPING_FILE, mapping)
    result["zuordnung_anpassen"] = str(MAPPING_FILE)
    return result


def summary_line(r: dict) -> str:
    parts = [f"{len(r['neu'])} neu", f"{len(r['aktualisiert'])} aktualisiert"]
    if r["neben_lokaler_aenderung"]:
        parts.append(f"{len(r['neben_lokaler_aenderung'])} neben lokal geänderten Dateien")
    if r["fehler"]:
        parts.append(f"{len(r['fehler'])} Fehler")
    return ", ".join(parts)
