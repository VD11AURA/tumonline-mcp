"""Moodle-Funktionen mit simulierten API-Antworten (Format der Moodle-Webservice-API)."""

import asyncio
import os
import time
from pathlib import Path

import pytest

from tumonline_mcp import moodle, moodle_sync, watch

NOW = time.time()
COURSES = [
    {"id": 11, "fullname": "Analysis 1 (MA0001)", "shortname": "Ana1", "startdate": NOW - 86400, "enddate": NOW + 90 * 86400},
    {"id": 12, "fullname": "Lineare Algebra 1", "shortname": "LA1-WS26", "startdate": NOW - 86400, "enddate": 0},
    {"id": 13, "fullname": "Alter Kurs", "shortname": "alt", "startdate": NOW - 400 * 86400, "enddate": NOW - 200 * 86400},
]


def file(name, t=1000, size=10, path="/"):
    return {"type": "file", "filename": name, "filepath": path, "filesize": size, "timemodified": t,
            "fileurl": f"https://moodle.example/pluginfile.php/{name}?forcedownload=1"}


CONTENTS = {
    11: [
        {"name": "Übungen", "modules": [
            {"modname": "resource", "name": "Übungsblatt 1", "contents": [file("blatt01.pdf")]},
            {"modname": "folder", "name": "Lösungen", "contents": [file("loesung01.pdf", path="/Woche 1/")]},
            {"modname": "url", "name": "Zoom", "contents": [{"type": "url", "fileurl": "https://zoom"}]},
        ]},
        {"name": "Vorlesung", "modules": [
            {"modname": "resource", "name": "Skript Kapitel 1", "contents": [file("skript.pdf")]},
            {"modname": "resource", "name": "Organisatorisches", "contents": [file("orga.pdf")]},
            {"modname": "resource", "name": "Probeklausur", "contents": [file("probe.pdf")]},
            {"modname": "resource", "name": "Video", "contents": [file("vl.mp4", size=500 * 2**20)]},
        ]},
    ],
    12: [{"name": "Allgemein", "modules": [{"modname": "resource", "name": "Blatt 1", "contents": [file("la_blatt1.pdf")]}]}],
}


@pytest.fixture
def fake_moodle(tmp_path, monkeypatch):
    sem = tmp_path / "Studium" / "01_WS2627"
    for d in ("Analysis1/Übungen", "Analysis1/Skript", "Analysis1/Mitschriften", "LA1"):
        (sem / d).mkdir(parents=True)
    monkeypatch.setenv("MOODLE_SYNC_DIR", str(sem))
    monkeypatch.setenv("MOODLE_TOKEN", "test")
    monkeypatch.setattr(moodle_sync, "MAPPING_FILE", tmp_path / "state" / "mapping.json")
    monkeypatch.setattr(moodle_sync, "SYNC_STATE_FILE", tmp_path / "state" / "sync.json")
    monkeypatch.setattr(watch, "WATCH_STATE_FILE", tmp_path / "state" / "watch.json")
    contents = {k: [dict(s, modules=[dict(m, contents=[dict(c) for c in m["contents"]]) for m in s["modules"]]) for s in v]
                for k, v in CONTENTS.items()}

    async def raw_courses():
        return COURSES

    async def call(function, ttl=0, **params):
        if function == "core_course_get_contents":
            return contents[params["courseid"]]
        raise AssertionError(function)

    downloads = []

    async def file_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * 10)
        downloads.append(dest)
        return 10

    monkeypatch.setattr(moodle, "raw_courses", raw_courses)
    monkeypatch.setattr(moodle, "call", call)
    monkeypatch.setattr(moodle, "file_download", file_download)
    return sem, contents, downloads


def test_sync_sorts_into_existing_folders(fake_moodle):
    sem, _, downloads = fake_moodle
    r = asyncio.run(moodle_sync.sync())
    got = {p.relative_to(sem).as_posix() for p in downloads}
    assert got == {
        "Analysis1/Übungen/blatt01.pdf",
        "Analysis1/Übungen/Lösungen/Woche 1/loesung01.pdf",
        "Analysis1/Skript/skript.pdf",
        "Analysis1/Moodle-Sonstiges/orga.pdf",
        "Analysis1/Altklausuren/probe.pdf",
        "LA1/Übungen/la_blatt1.pdf",
    }
    assert any("vl.mp4" in x for x in r["uebersprungen_zu_gross"])
    assert [k["kurs"] for k in r["kurse"]] == ["Analysis 1 (MA0001)", "Lineare Algebra 1"]  # alter Kurs fehlt


def test_sync_is_incremental_and_protects_local_edits(fake_moodle):
    sem, contents, downloads = fake_moodle
    asyncio.run(moodle_sync.sync())
    downloads.clear()
    assert asyncio.run(moodle_sync.sync())["neu"] == [] and downloads == []

    # Moodle aktualisiert zwei Dateien; eine davon wurde lokal verändert (Notizen)
    mods = contents[11][0]["modules"]
    mods[0]["contents"][0]["timemodified"] = 2000
    contents[11][1]["modules"][0]["contents"][0]["timemodified"] = 2000
    (sem / "Analysis1/Skript/skript.pdf").write_bytes(b"mit notizen")
    r = asyncio.run(moodle_sync.sync())
    assert r["aktualisiert"] == ["Analysis1/Übungen/blatt01.pdf"]
    assert len(r["neben_lokaler_aenderung"]) == 1 and "skript (Moodle" in r["neben_lokaler_aenderung"][0]
    assert (sem / "Analysis1/Skript/skript.pdf").read_bytes() == b"mit notizen"


def test_dry_run_downloads_nothing(fake_moodle):
    _, _, downloads = fake_moodle
    r = asyncio.run(moodle_sync.sync(dry_run=True))
    assert downloads == [] and len(r["neu"]) == 6


def test_mapping_can_be_disabled(fake_moodle):
    _, _, downloads = fake_moodle
    asyncio.run(moodle_sync.sync(dry_run=True))
    import json
    m = json.loads(moodle_sync.MAPPING_FILE.read_text())
    m["12"]["aktiv"] = False
    moodle_sync.MAPPING_FILE.write_text(json.dumps(m))
    asyncio.run(moodle_sync.sync())
    assert not any("LA1" in str(p) for p in downloads)


def test_grades_and_announcements(monkeypatch):
    monkeypatch.setenv("MOODLE_TOKEN", "test")

    async def call(function, ttl=0, **params):
        if function == "core_webservice_get_site_info":
            return {"userid": 7, "fullname": "Test"}
        if function == "gradereport_user_get_grade_items":
            return {"usergrades": [{"gradeitems": [
                {"itemname": "Blatt 1", "itemtype": "mod", "itemmodule": "assign", "graderaw": 8, "grademax": 10, "gradeformatted": "8,00"},
                {"itemname": "Blatt 2", "itemtype": "mod", "itemmodule": "assign", "graderaw": None, "grademax": 10, "gradeformatted": "-"},
                {"itemname": None, "itemtype": "course", "graderaw": 8, "grademax": 20, "gradeformatted": "8,00"},
            ]}]}
        if function == "mod_forum_get_forums_by_courses":
            assert params["courseids"] == [11]
            return [{"id": 5, "course": 11, "type": "news"}, {"id": 6, "course": 11, "type": "general"}]
        if function == "mod_forum_get_forum_discussions":
            assert params["forumid"] == 5
            return {"discussions": [
                {"discussion": 99, "subject": "Übung fällt aus", "message": "<p>Am <b>Montag</b> keine Übung.</p>",
                 "userfullname": "Prof", "timemodified": NOW - 3600, "pinned": False},
                {"discussion": 98, "subject": "Alt", "message": "", "timemodified": NOW - 60 * 86400},
            ]}
        raise AssertionError(function)

    monkeypatch.setattr(moodle, "call", call)
    g = asyncio.run(moodle.grades(11))
    assert (g["summe_punkte"], g["summe_max_bewertet"], g["summe_max_alle"], g["bewertet"]) == (8, 10, 20, 1)
    posts = asyncio.run(moodle.announcements([11], days=14))
    assert [p["titel"] for p in posts] == ["Übung fällt aus"]
    assert posts[0]["text"] == "Am Montag keine Übung."


def test_watch_baseline_then_notifies(fake_moodle, monkeypatch):
    notes = [{"lv_nummer": "1", "datum": "2027-02-20", "lv_titel": "Analysis 1", "uninotenamekurz": "1,7"}]
    events = [{"nr": "e1", "title": "LA Übung", "dtstart": f"{time.strftime('%Y-%m-%d')} 23:59:00",
               "location": "Raum A (5602.EG.001)", "status": "FT"}]
    sent = []

    async def records(endpoint, **params):
        return {"noten": notes, "kalender": events}[endpoint]

    async def announcements(ids, days):
        return []

    monkeypatch.setattr(watch.client, "records", records)
    monkeypatch.setattr(moodle, "announcements", announcements)
    monkeypatch.setattr(watch, "notify", lambda title, msg, dry_run=False: sent.append((title, msg)))

    asyncio.run(watch.run_once())
    # erster Lauf: keine Einzelmeldungen, nur Datei-Abgleich + Startmeldung
    assert [t for t, _ in sent] == ["6 neue Moodle-Dateien", "TUM-Wächter aktiv"]
    sent.clear()

    notes.append({"lv_nummer": "2", "datum": "2027-02-25", "lv_titel": "Lineare Algebra 1", "uninotenamekurz": "2,0"})
    events[0] = dict(events[0], location="Raum B (5602.EG.002)")
    asyncio.run(watch.run_once())
    assert ("Neue Note", "Lineare Algebra 1: 2,0") in sent
    assert any(t == "Raum geändert" for t, _ in sent)
    assert not any(t == "TUM-Wächter aktiv" for t, _ in sent)


def test_moodle_added_later_does_not_flood(fake_moodle, monkeypatch):
    sent = []
    monkeypatch.setattr(watch, "notify", lambda title, msg, dry_run=False: sent.append(title))
    watch._save(watch.WATCH_STATE_FILE, {"noten": [], "termine": {}})  # Wächter lief schon ohne Moodle

    async def records(endpoint, **params):
        return []

    async def announcements(ids, days):
        return [{"id": i, "kurs_id": 11, "titel": f"Post {i}", "zeitstempel": NOW} for i in range(5)]

    monkeypatch.setattr(watch.client, "records", records)
    monkeypatch.setattr(moodle, "announcements", announcements)
    monkeypatch.setenv("MOODLE_AUTO_SYNC", "0")
    asyncio.run(watch.run_once())
    assert sent == []
