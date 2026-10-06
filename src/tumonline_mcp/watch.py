"""Hintergrund-Wächter: meldet Neues per macOS-Mitteilung. Gedacht für launchd (stündlich).

Prüft:
- TUMonline: neue Noten; abgesagte oder verlegte Termine (Zeit/Raum) in den nächsten 14 Tagen
- Moodle (falls Token gesetzt): neue Ankündigungen; lädt neue Dateien in die Studium-Ordner
  (abschaltbar mit MOODLE_AUTO_SYNC=0)

Beim ersten Lauf jeder Prüfung (auch wenn z. B. der Moodle-Token später dazukommt) wird nur
der aktuelle Stand gemerkt, damit keine Flut an Meldungen kommt.
Aufruf: uv run tum-watch [--dry-run] [--test-notification]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import date, datetime, timedelta

from . import client, moodle, moodle_sync
from .moodle_sync import STATE_DIR, _load, _save

WATCH_STATE_FILE = STATE_DIR / "watch.json"


def notify(title: str, message: str, dry_run: bool = False) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] {title}: {message}")
    if dry_run:
        return
    script = f"display notification {json.dumps(message[:220])} with title {json.dumps(title)} sound name \"default\""
    subprocess.run(["/usr/bin/osascript", "-e", script], check=False, capture_output=True)


def grade_key(r: dict) -> str:
    return "|".join(r.get(k, "") for k in ("lv_nummer", "datum", "lv_titel", "exam_typ_name"))


async def check_grades(state: dict, dry_run: bool) -> None:
    first_run = "noten" not in state
    rows = await client.records("noten")
    seen = set(state.get("noten", []))
    new = [r for r in rows if grade_key(r) not in seen]
    if not first_run:
        for r in new:
            note = r.get("uninotenamekurz", "")
            notify("Neue Note", f"{r.get('lv_titel', '?')}: {note}" if note else r.get("lv_titel", "?"), dry_run)
    state["noten"] = sorted(seen | {grade_key(r) for r in rows})


async def check_calendar(state: dict, dry_run: bool) -> None:
    first_run = "termine" not in state
    today = date.today()
    rows = await client.records("kalender", pMonateVor=0, pMonateNach=1)
    old = state.get("termine", {})
    current = {}
    for r in rows:
        start = r.get("dtstart", "")
        if not r.get("nr") or not (today.isoformat() <= start[:10] <= (today + timedelta(days=14)).isoformat()):
            continue
        current[r["nr"]] = {"titel": r.get("title", ""), "beginn": start, "ort": r.get("location", ""),
                            "status": r.get("status", "")}
    if not first_run:
        for nr, ev in current.items():
            before = old.get(nr)
            when = ev["beginn"][:16].replace("T", " ")
            if ev["status"].upper() == "CANCEL" and (not before or before["status"].upper() != "CANCEL"):
                notify("Termin abgesagt", f"{ev['titel']} am {when}", dry_run)
            elif before and before["beginn"] != ev["beginn"]:
                notify("Termin verlegt", f"{ev['titel']}: jetzt {when} (vorher {before['beginn'][:16]})", dry_run)
            elif before and before["ort"] != ev["ort"]:
                notify("Raum geändert", f"{ev['titel']} am {when}: {ev['ort'].split(' (')[0]}", dry_run)
    state["termine"] = current


async def check_moodle(state: dict, dry_run: bool) -> None:
    first_run = "ankuendigungen" not in state
    courses = [c for c in await moodle.raw_courses() if moodle.is_current(c)]
    names = {c["id"]: c.get("shortname") or c.get("fullname", "") for c in courses}
    posts = await moodle.announcements(list(names), days=30)
    seen = set(state.get("ankuendigungen", []))
    if not first_run:
        for p in posts:
            if str(p["id"]) not in seen:
                notify(f"Moodle: {names.get(p['kurs_id'], 'Ankündigung')}", p["titel"], dry_run)
    state["ankuendigungen"] = sorted(seen | {str(p["id"]) for p in posts})

    if client.setting("MOODLE_AUTO_SYNC", "1") != "0":
        result = await moodle_sync.sync(dry_run=dry_run)
        count = len(result["neu"]) + len(result["aktualisiert"]) + len(result["neben_lokaler_aenderung"])
        if count:
            files = result["neu"] + result["aktualisiert"] + result["neben_lokaler_aenderung"]
            preview = ", ".join(f.split("/")[-1] for f in files[:3]) + (" …" if count > 3 else "")
            notify(f"{count} neue Moodle-Datei{'en' if count > 1 else ''}", preview, dry_run)
        for err in result["fehler"]:
            print(f"Sync-Fehler: {err}", file=sys.stderr)


async def run_once(dry_run: bool = False) -> int:
    state = _load(WATCH_STATE_FILE)
    first_run = not state
    errors = 0
    checks = [check_grades, check_calendar]
    if moodle.configured():
        checks.append(check_moodle)
    for check in checks:
        try:
            await check(state, dry_run)
        except Exception as e:  # noqa: BLE001 - ein Dienst darf die anderen nicht blockieren
            errors += 1
            print(f"{check.__name__}: {e}", file=sys.stderr)
    state["letzter_lauf"] = datetime.now().isoformat(timespec="seconds")
    if not dry_run:
        _save(WATCH_STATE_FILE, state)
    if first_run:
        notify("TUM-Wächter aktiv", "Ab jetzt melde ich neue Noten, Terminänderungen und Moodle-Neuigkeiten.", dry_run)
    print(f"Lauf beendet ({len(checks)} Prüfungen, {errors} Fehler).")
    return 1 if errors == len(checks) else 0


def main() -> None:
    p = argparse.ArgumentParser(prog="tum-watch", description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts speichern/laden/melden")
    p.add_argument("--test-notification", action="store_true", help="nur eine Test-Mitteilung anzeigen")
    args = p.parse_args()
    if args.test_notification:
        notify("TUM-Wächter", "Test: Mitteilungen funktionieren.")
        return
    sys.exit(asyncio.run(run_once(args.dry_run)))


if __name__ == "__main__":
    main()
