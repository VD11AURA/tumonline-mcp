"""Kommandozeile für den Moodle-Datei-Abgleich: uv run moodle-sync [--dry-run]."""

from __future__ import annotations

import argparse
import asyncio
import sys

from . import moodle_sync


def main() -> None:
    p = argparse.ArgumentParser(prog="moodle-sync", description=moodle_sync.__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, was geladen würde")
    args = p.parse_args()
    try:
        r = asyncio.run(moodle_sync.sync(dry_run=args.dry_run))
    except Exception as e:  # noqa: BLE001 - verständliche Meldung statt Traceback
        print(e)
        sys.exit(1)
    print(f"Semesterordner: {r['semesterordner']}")
    for k in r["kurse"]:
        print(f"  {k['kurs']}  →  {k['ordner']}{'' if k['aktiv'] else '  (deaktiviert)'}")
    for label, key in (("Neu", "neu"), ("Aktualisiert", "aktualisiert"),
                       ("Neben lokal geänderter Datei", "neben_lokaler_aenderung"),
                       ("Zu groß, übersprungen", "uebersprungen_zu_gross"), ("Fehler", "fehler")):
        for item in r[key]:
            print(f"{label}: {item}")
    print(("Probelauf: " if args.dry_run else "") + moodle_sync.summary_line(r))
    print(f"Zuordnung Kurs → Ordner anpassen in: {r['zuordnung_anpassen']}")


if __name__ == "__main__":
    main()
