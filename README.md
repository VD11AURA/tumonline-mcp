# tumonline-mcp

Lokaler MCP-Server (stdio) fürs TUM-Studium: persönliche TUMonline-Daten über die
CAMPUSonline-Webservices (`wbservicesbasic.*`), Räume über NavigaTUM, Speisepläne über
die TUM Eat-API und Moodle über die Moodle-Webservice-API.

## Tools

| Tool | Inhalt | Quelle |
|---|---|---|
| `status` | Token gesetzt/gültig für TUMonline und Moodle? | |
| `kalender(von?, bis?)` | Persönlicher Kalender mit Kartenlink je Raum, Standard: nächste 7 Tage | TUMonline |
| `naechster_termin()` | Nächster Termin inkl. Adresse, Stockwerk, Karte | TUMonline + NavigaTUM |
| `kurse(semester?)` | Eigene Lehrveranstaltungen | TUMonline |
| `kurs_details(lv_nr, gruppe?, alle_termine?)` | Details + Termine, optional nur eine Übungsgruppe | TUMonline |
| `veranstaltung_suchen(suchbegriff)` | Katalogsuche | TUMonline |
| `pruefungen(von?, bis?)` | Prüfungstermine aus dem Kalender (Titel-Erkennung), Standard: 6 Monate | TUMonline |
| `noten()` | Prüfungsergebnisse | TUMonline |
| `raum(suche)` | Raum/Gebäude: Adresse, Stockwerk, NavigaTUM- und Google-Maps-Link | NavigaTUM |
| `mensa(mensa_id?, datum?, nur_vegetarisch?)` | Speiseplan mit Studierendenpreisen | Eat-API |
| `mensen()` | Alle Mensen mit ID und Öffnungszeiten | Eat-API |
| `moodle_kurse()` | Eigene Moodle-Kurse | Moodle |
| `moodle_fristen(tage?)` | Anstehende Abgaben/Quizze/Fristen | Moodle |
| `moodle_kursinhalt(kurs_id)` | Abschnitte, Materialien, Dateinamen | Moodle |
| `moodle_ankuendigungen(tage?, kurs_id?)` | Ankündigungen der Lehrenden (Nachrichtenforen) | Moodle |
| `moodle_punkte(kurs_id?)` | Übungspunkte/Bewertungen mit Summen | Moodle |
| `moodle_dateien_sync(probelauf?)` | Lädt neue Moodle-Dateien in die Studium-Ordner | Moodle |

TUMonline-Antworten werden 5 Minuten zwischengespeichert, Raumdaten 24 Stunden, Speisepläne 1 Stunde.

## Setup

```sh
uv sync
cp .env.example .env
uv run tumonline-token <TUM-Kennung>      # fordert eigenen Token an, schreibt ihn in .env
# TUMonline → Visitenkarte → Token-Management → Token aktivieren, Rechte freigeben
uv run tumonline-token --check
uv run moodle-token                       # optional: Moodle-Token verdeckt eintragen und prüfen
```

`.env`-Schlüssel: `TUMONLINE_TOKEN`, `TUMONLINE_BASE_URL`, `MOODLE_TOKEN`, `MOODLE_BASE_URL`,
`MENSA_DEFAULT`, `STUDIUM_DIR`, `MOODLE_SYNC_DIR`, `MOODLE_SYNC_MAX_MB`, `MOODLE_AUTO_SYNC`
(alle außer `TUMONLINE_TOKEN` optional, siehe `.env.example`). Die `.env` wird bei jedem Aufruf
relativ zum Projektordner gelesen; neue Tokens greifen ohne Neustart.

Moodle-Token: moodle.tum.de → Profilbild → Einstellungen → Sicherheitsschlüssel →
Schlüssel für „Moodle mobile web service“.

## Moodle-Dateien in die Studium-Ordner

```sh
uv run moodle-sync --dry-run   # zeigt, was wohin geladen würde
uv run moodle-sync             # lädt
```

Ziel: `~/Studium/<Semesterordner>/<Fach>/<Kategorie>/`. Der Semesterordner wird am Namen
erkannt (`…_WS2627`, `…_SS27`), das Fach an vorhandenen Ordnern (`Analysis1`, `LA1`,
`ExPhysik1` …; sonst neuer Ordner mit Kursnamen), die Kategorie an Modul-/Dateinamen
(`Übungen`, `Skript`, `Altklausuren`, sonst `Moodle-Sonstiges`). `Mitschriften` wird nie
angefasst. Nur aktuelle Kurse, Dateien > 100 MB werden übersprungen.

Die Zuordnung Kurs → Ordner steht nach dem ersten Lauf in `.state/moodle_ordner.json`;
dort `"ordner"` ändern oder `"aktiv": false` setzen. Lokal veränderte Dateien werden nie
überschrieben – neue Moodle-Versionen landen daneben als `Name (Moodle JJJJ-MM-TT).pdf`.

## Wächter (Mitteilungen)

`tum-watch` meldet per macOS-Mitteilung: neue Noten, abgesagte/verlegte Termine und
Raumänderungen (nächste 14 Tage), neue Moodle-Ankündigungen und neu geladene Moodle-Dateien.

```sh
./scripts/install-watch.sh      # launchd-Job, stündlich (./scripts/install-watch.sh 30 = alle 30 Min)
./scripts/uninstall-watch.sh    # entfernen
uv run tum-watch --dry-run      # einmal testen, ohne zu speichern/melden
tail -f ~/Library/Logs/tumonline-watch.log
```

## Einbindung

Start: `uv run --directory <projektpfad> tumonline-mcp`.

```sh
claude mcp add -s user tumonline -- "$(which uv)" run --directory "$PWD" tumonline-mcp
```

Für Claude Desktop/Cowork in `~/Library/Application Support/Claude/claude_desktop_config.json`
unter `mcpServers` denselben Befehl mit absolutem Pfad zu `uv` eintragen.

## Tests

```sh
uv run pytest
```
