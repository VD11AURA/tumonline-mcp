"""MCP-Server für das TUM-Studium: TUMonline, Räume (NavigaTUM), Mensa und Moodle."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from mcp.server.mcpserver import MCPServer

from . import client, mensa as eat, moodle as mdl, moodle_sync, navigatum
from .client import TUMonlineError

mcp = MCPServer(
    name="tumonline",
    instructions=(
        "Tools für das TUM-Studium. TUMonline: Kalender/Stundenplan, eigene Lehrveranstaltungen, "
        "Prüfungstermine, Noten. NavigaTUM: Räume und Gebäude finden (Adresse, Karte). "
        "Mensa: Speisepläne der Studierendenwerk-Mensen. Moodle: Kurse, Abgabefristen, Materialien, "
        "Ankündigungen, Übungspunkte und Datei-Abgleich in die lokalen Studium-Ordner. "
        "Bei Fehlern zuerst `status` aufrufen."
    ),
)

EXAM_PATTERN = re.compile(
    r"prüfung|pruefung|klausur|exam|endterm|midterm|retake|wiederholung", re.IGNORECASE
)
DEFAULT_MENSA = "mensa-garching"


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _months_between(a: date, b: date) -> int:
    return max(0, (b.year - a.year) * 12 + b.month - a.month + 1)


def _event(e: dict[str, str]) -> dict:
    out = {
        "titel": e.get("title", ""),
        "beginn": e.get("dtstart", ""),
        "ende": e.get("dtend", ""),
        "ort": e.get("location", ""),
        "beschreibung": e.get("description", ""),
        "status": e.get("status", ""),
        "abgesagt": e.get("status", "").upper() == "CANCEL",
        "url": e.get("url", ""),
    }
    if room := navigatum.room_id_from_location(out["ort"]):
        out["raum_id"] = room
        out["karte"] = navigatum.map_url(room)
    return out


async def _calendar(start: date, end: date) -> list[dict]:
    today = date.today()
    raw = await client.records(
        "kalender",
        pMonateVor=_months_between(start, today) if start < today else 0,
        pMonateNach=_months_between(today, end) if end > today else 0,
    )
    events = []
    for e in raw:
        dt = _parse_dt(e.get("dtstart", ""))
        if dt and start <= dt.date() <= end:
            events.append((dt, _event(e)))
    events.sort(key=lambda x: x[0])
    return [ev for _, ev in events]


def _range(von: str | None, bis: str | None, default_days: int) -> tuple[date, date]:
    try:
        start = date.fromisoformat(von) if von else date.today()
        end = date.fromisoformat(bis) if bis else start + timedelta(days=default_days)
    except ValueError as e:
        raise TUMonlineError("Datum bitte im Format YYYY-MM-DD angeben.") from e
    if end < start:
        raise TUMonlineError("'bis' liegt vor 'von'.")
    return start, end


def current_semester(today: date | None = None) -> str:
    """Studiensemester im TUMonline-Format: '26W' von September bis März, sonst '26S'.

    Ab September zählt schon das kommende Wintersemester (Vorkurse, Anmeldung),
    obwohl TUMonline intern noch das Sommersemester als aktuell führt.
    """
    d = today or date.today()
    if d.month >= 9:
        return f"{d.year % 100:02d}W"
    if d.month <= 3:
        return f"{(d.year - 1) % 100:02d}W"
    return f"{d.year % 100:02d}S"


def _semester_rank(semester_id: str) -> int | None:
    """'26W' → 53, '26S' → 52 (fortlaufend, zum Vergleichen)."""
    if len(semester_id) == 3 and semester_id[:2].isdigit() and semester_id[2] in "SW":
        return int(semester_id[:2]) * 2 + (semester_id[2] == "W")
    return None


def _normalize_semester(value: str) -> str:
    v = value.strip().upper().replace(" ", "")
    if len(v) == 3 and v[:2].isdigit() and v[2] in "SW":
        return v
    raise TUMonlineError("Semester bitte im Format '26W' (Wintersemester 2026/27) oder '26S' angeben.")


def _termin(t: dict[str, str]) -> dict:
    return {
        "beginn": t.get("beginn_datum_zeitpunkt", ""),
        "ende": t.get("ende_datum_zeitpunkt", ""),
        "ort": t.get("ort", ""),
        "raum_suche": t.get("raum_nr_architekt", ""),
        "art": t.get("art", ""),
        "betreff": t.get("termin_betreff", ""),
        "gruppe": t.get("lv_grp_name", ""),
    }


async def _termine_aus_kalender(lv_nr: str, alle: bool) -> list[dict]:
    """Ersatzquelle: TUMonline liefert für LVs ohne Gruppen keine Terminliste, der
    persönliche Kalender verlinkt die Termine aber mit cLvNr=<stp_sp_nr>."""
    today = date.today()
    start = today - timedelta(days=62) if alle else today
    out = []
    for ev in await _calendar(start, today + timedelta(days=183)):
        if re.search(rf"[?&]cLvNr={re.escape(lv_nr)}(?:&|$)", ev["url"]):
            out.append({
                "beginn": ev["beginn"][:16],
                "ende": ev["ende"][:16],
                "ort": ev["ort"],
                "raum_id": ev.get("raum_id", ""),
                "karte": ev.get("karte", ""),
                "art": "abgesagt" if ev["abgesagt"] else "Abhaltung",
                "betreff": ev["beschreibung"],
                "gruppe": "",
            })
    return out


def _course(r: dict[str, str]) -> dict:
    return {
        "lv_nr": r.get("stp_sp_nr", ""),  # interne Nummer, die kurs_details erwartet
        "titel": r.get("stp_sp_titel", ""),
        "art": r.get("stp_lv_art_name", ""),
        "sws": r.get("dauer_info", ""),
        "semester": r.get("semester_name") or r.get("semester_id", ""),
        "semester_id": r.get("semester_id", ""),
        "organisation": r.get("org_name_betreut", ""),
        "vortragende": r.get("vortragende_mitwirkende", ""),
    }


# --- Status ---------------------------------------------------------------


@mcp.tool()
async def status() -> dict:
    """Prüft Konfiguration und Verbindung zu TUMonline und Moodle (Token gesetzt/gültig?)."""
    info: dict = {"env_datei": str(client.ENV_FILE), "env_datei_vorhanden": client.ENV_FILE.exists()}

    tum: dict = {"base_url": client.base_url(), "token_gesetzt": bool(client.token())}
    if not tum["token_gesetzt"]:
        tum.update(ok=False, hinweis="TUMONLINE_TOKEN fehlt. Einrichten mit `tumonline-token <TUM-Kennung>`.")
    else:
        try:
            root = await client.call("isTokenConfirmed")
            confirmed = (root.text or "").strip().lower() == "true"
            tum.update(token_bestaetigt=confirmed, ok=confirmed)
            if not confirmed:
                tum["hinweis"] = (
                    "Token ist nicht aktiviert. In TUMonline: Visitenkarte → Token-Management "
                    "→ Token 'Claude MCP …' aktivieren und Rechte freigeben."
                )
        except Exception as e:  # noqa: BLE001 - status soll nie werfen
            tum.update(ok=False, fehler=str(e))
    info["tumonline"] = tum

    moo: dict = {"base_url": mdl.base_url(), "token_gesetzt": bool(mdl.token())}
    if not moo["token_gesetzt"]:
        moo.update(ok=False, hinweis="MOODLE_TOKEN fehlt (optional). Einrichten mit `moodle-token`.")
    else:
        try:
            site = await mdl.site_info()
            moo.update(ok=True, benutzer=site.get("fullname", ""))
        except Exception as e:  # noqa: BLE001
            moo.update(ok=False, fehler=str(e))
    info["moodle"] = moo
    info["ok"] = tum["ok"]
    return info


# --- TUMonline ------------------------------------------------------------


@mcp.tool()
async def kalender(von: str | None = None, bis: str | None = None) -> list[dict]:
    """Persönlicher TUMonline-Kalender (Vorlesungen, Übungen, Prüfungen, eigene Termine).

    von/bis im Format YYYY-MM-DD. Standard: heute bis in 7 Tagen.
    Einträge mit Raum enthalten raum_id und einen Kartenlink (NavigaTUM).
    """
    start, end = _range(von, bis, 7)
    return await _calendar(start, end)


@mcp.tool()
async def naechster_termin() -> dict:
    """Der nächste anstehende (nicht abgesagte) Termin inkl. Raumdetails, Adresse und Kartenlink."""
    now = datetime.now()
    for ev in await _calendar(now.date(), now.date() + timedelta(days=30)):
        if ev["abgesagt"] or (_parse_dt(ev["beginn"]) or now) < now:
            continue
        if ev.get("raum_id"):
            try:
                ev["raum"] = await navigatum.location(ev["raum_id"])
            except Exception:  # noqa: BLE001 - Raumdetails sind optional
                pass
        return ev
    return {"hinweis": "Keine Termine in den nächsten 30 Tagen."}


@mcp.tool()
async def kurse(semester: str | None = None) -> list[dict]:
    """Eigene Lehrveranstaltungen (für die man angemeldet ist).

    semester optional als Filter, z. B. '26W' oder 'Wintersemester 2026/27'.
    """
    out = [_course(r) for r in await client.records("veranstaltungenEigene")]
    if semester:
        s = semester.lower()
        out = [k for k in out if s in k["semester"].lower() or s in k["semester_id"].lower()]
    return out


@mcp.tool()
async def kurs_details(lv_nr: str, gruppe: str | None = None, alle_termine: bool = False) -> dict:
    """Details und Termine einer Lehrveranstaltung.

    lv_nr ist die interne TUMonline-Nummer (stp_sp_nr), also genau das Feld `lv_nr` aus
    `kurse` oder `veranstaltung_suchen` bzw. die Zahl hinter cLvNr= in Kalender-Links
    (z. B. 950944616). Andere Nummern (z. B. stp_lv_nr) können einen fremden, alten Kurs
    treffen; das Ergebnis enthält dann eine Warnung.
    gruppe: nur Termine dieser Übungsgruppe, z. B. '02' oder 'Gruppe 02'.
    Standard: nur kommende Termine; alle_termine=True liefert auch vergangene.
    """
    details_rows = await client.records("veranstaltungenDetails", pLVNr=lv_nr)
    details = details_rows[0] if details_rows else {}
    warnungen = []
    if not details:
        warnungen.append(f"Keine Lehrveranstaltung mit stp_sp_nr {lv_nr} gefunden.")
    else:
        if details.get("stp_sp_nr") and details["stp_sp_nr"] != lv_nr:
            warnungen.append(
                f"{lv_nr} ist keine stp_sp_nr; TUMonline hat stattdessen die LV mit stp_sp_nr "
                f"{details['stp_sp_nr']} geliefert. Nummer aus `kurse`/`veranstaltung_suchen` (Feld lv_nr) verwenden."
            )
        rank, current = _semester_rank(details.get("semester_id", "")), _semester_rank(current_semester())
        if rank is not None and current is not None and rank < current - 1:
            warnungen.append(
                f"Achtung: Diese LV ist aus dem {details.get('semester_name') or details.get('semester_id')} "
                "und damit älter als das Vorsemester – vermutlich die falsche Nummer."
            )

    quelle = "TUMonline-Terminliste"
    try:
        roh = await client.records("veranstaltungenTermine", pLVNr=lv_nr)
    except TUMonlineError as e:
        roh, warnungen = [], [*warnungen, f"Terminliste nicht abrufbar: {e}"]
    termine = [_termin(t) for t in roh]
    if not alle_termine:
        today = date.today()
        termine = [t for t in termine if (dt := _parse_dt(t["beginn"])) is None or dt.date() >= today]
    if not roh:
        termine = await _termine_aus_kalender(lv_nr, alle_termine)
        quelle = "persönlicher Kalender (TUMonline liefert für diese LV keine Terminliste)"
        if not termine:
            warnungen.append(
                "TUMonline liefert für diese LV keine Terminliste, und im persönlichen Kalender stehen "
                "keine Termine dazu (nur für LVs, für die du angemeldet bist). Siehe details.ersttermin."
            )
    if gruppe:
        g = gruppe.lower().removeprefix("gruppe").strip()
        termine = [t for t in termine if g in t["gruppe"].lower()]
    gruppen = sorted({t["gruppe"] for t in termine if t["gruppe"]})
    out = {"details": details, "gruppen": gruppen, "termine_quelle": quelle, "termine": termine}
    if warnungen:
        out["warnungen"] = warnungen
    return out


@mcp.tool()
async def veranstaltung_suchen(suchbegriff: str, semester: str | None = None) -> list[dict]:
    """Sucht Lehrveranstaltungen im TUMonline-Katalog nach Titel.

    semester im Format '26W' (Wintersemester 2026/27) oder '26S'. Standard: das aktuelle
    Studiensemester (ab September schon das kommende Wintersemester).
    Das Feld lv_nr im Ergebnis ist die Nummer für `kurs_details`.
    """
    sem = _normalize_semester(semester) if semester else current_semester()
    rows = await client.records("veranstaltungenSuche", pSuche=suchbegriff, pSemester=sem)
    return [_course(r) for r in rows]


@mcp.tool()
async def pruefungen(von: str | None = None, bis: str | None = None) -> list[dict]:
    """Prüfungs-/Klausurtermine aus dem persönlichen Kalender.

    Erkennt Einträge über den Titel (Prüfung, Klausur, Exam, Endterm, Retake …).
    von/bis im Format YYYY-MM-DD. Standard: heute bis in 6 Monaten.
    """
    start, end = _range(von, bis, 183)
    events = await _calendar(start, end)
    return [e for e in events if EXAM_PATTERN.search(f"{e['titel']} {e['beschreibung']}")]


@mcp.tool()
async def noten() -> list[dict]:
    """Prüfungsergebnisse/Noten aus TUMonline (neueste zuerst)."""
    rows = await client.records("noten")
    out = [
        {
            "datum": r.get("datum", ""),
            "titel": r.get("lv_titel", ""),
            "lv_nr": r.get("lv_nummer", ""),
            "semester": r.get("lv_semester", ""),
            "note": r.get("uninotenamekurz", ""),
            "credits": r.get("lv_credits", ""),
            "pruefer": r.get("pruefer_nachname", ""),
            "art": r.get("exam_typ_name", ""),
            "modus": r.get("modus", ""),
            "studium": r.get("studienbezeichnung", ""),
        }
        for r in rows
    ]
    out.sort(key=lambda n: n["datum"], reverse=True)
    return out


# --- Räume ----------------------------------------------------------------


@mcp.tool()
async def raum(suche: str) -> dict:
    """Findet Räume/Gebäude der TUM über NavigaTUM: Adresse, Stockwerk, Karten- und Google-Maps-Link.

    suche: Raum-ID (5602.EG.001), Architektenname (02.04.011@5604) oder Freitext ('MI HS 1', 'Mensa Garching').
    """
    return await navigatum.resolve(suche)


# --- Mensa ----------------------------------------------------------------


@mcp.tool()
async def mensa(mensa_id: str | None = None, datum: str | None = None, nur_vegetarisch: bool = False) -> dict:
    """Speiseplan einer Mensa mit Studierendenpreisen.

    mensa_id aus `mensen` (Standard: MENSA_DEFAULT aus .env, sonst mensa-garching).
    datum im Format YYYY-MM-DD (Standard: heute).
    """
    try:
        day = date.fromisoformat(datum) if datum else date.today()
    except ValueError as e:
        raise TUMonlineError("Datum bitte im Format YYYY-MM-DD angeben.") from e
    return await eat.menu(mensa_id or client.setting("MENSA_DEFAULT", DEFAULT_MENSA), day, nur_vegetarisch)


@mcp.tool()
async def mensen() -> list[dict]:
    """Alle Mensen/Cafeterien mit ID, Adresse und Öffnungszeiten."""
    return await eat.canteens()


# --- Moodle ---------------------------------------------------------------


@mcp.tool()
async def moodle_kurse(nur_aktuelle: bool = True) -> list[dict]:
    """Eigene Moodle-Kurse der TUM (kurs_id für die anderen moodle_*-Tools).

    nur_aktuelle=False zeigt auch Kurse vergangener Semester.
    """
    return await mdl.courses(nur_aktuelle)


@mcp.tool()
async def moodle_fristen(tage: int = 14) -> list[dict]:
    """Anstehende Moodle-Abgaben, Quizze und Fristen der nächsten `tage` Tage, sortiert nach Fälligkeit."""
    return await mdl.deadlines(max(1, min(tage, 180)))


@mcp.tool()
async def moodle_kursinhalt(kurs_id: int) -> list[dict]:
    """Abschnitte, Materialien und Dateinamen eines Moodle-Kurses (mit Änderungsdatum)."""
    return await mdl.course_contents(kurs_id)


@mcp.tool()
async def moodle_ankuendigungen(tage: int = 14, kurs_id: int | None = None) -> list[dict]:
    """Ankündigungen der Lehrenden (Nachrichtenforen) der letzten `tage` Tage, neueste zuerst.

    Ohne kurs_id: alle aktuellen Kurse.
    """
    ids = [kurs_id] if kurs_id else [c["kurs_id"] for c in await mdl.courses(nur_aktuelle=True)]
    return await mdl.announcements(ids, max(1, min(tage, 365)))


@mcp.tool()
async def moodle_punkte(kurs_id: int | None = None) -> list[dict]:
    """Bewertungen/Übungspunkte aus Moodle mit Summe erreichter und möglicher Punkte.

    Ohne kurs_id: alle aktuellen Kurse. Ob ein Bonus erreicht ist, hängt von der
    Bonusregel des Kurses ab (steht meist in der Kursbeschreibung/Ankündigung).
    """
    courses = await mdl.courses(nur_aktuelle=True)
    names = {c["kurs_id"]: c["name"] for c in courses}
    out = []
    for cid in [kurs_id] if kurs_id else list(names):
        try:
            g = await mdl.grades(cid)
        except mdl.ServiceError as e:
            g = {"kurs_id": cid, "fehler": str(e)}
        g["kurs"] = names.get(cid, "")
        out.append(g)
    return out


@mcp.tool()
async def moodle_dateien_sync(probelauf: bool = False) -> dict:
    """Lädt neue/geänderte Moodle-Dateien der aktuellen Kurse in ~/Studium/<Semester>/<Fach>/…

    Sortiert nach Übungen / Skript / Altklausuren / Moodle-Sonstiges. Lokal geänderte
    Dateien werden nie überschrieben. probelauf=True zeigt nur an, was geladen würde.
    Die Zuordnung Kurs → Ordner steht in .state/moodle_ordner.json und ist anpassbar.
    """
    return await moodle_sync.sync(dry_run=probelauf)


def run() -> None:
    mcp.run("stdio")
