"""Token-Einrichtung für TUMonline und Moodle; Tokens werden nie angezeigt.

TUMonline: fordert einen neuen Token an und schreibt ihn in die .env, ohne ihn anzuzeigen.

Aufruf:  tumonline-token <TUM-Kennung>      # neuen Token anfordern
         tumonline-token --check            # prüfen, ob er aktiviert ist
         moodle-token [--check]             # Moodle-Token verdeckt eintragen/prüfen
(im Git-Checkout jeweils mit `uv run` davor)
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys

from dotenv import set_key

from . import client

TOKEN_NAME = f"Claude MCP {socket.gethostname().removesuffix('.local')}"[:40]


def ensure_env_file() -> None:
    """Legt die .env (nur für den Benutzer lesbar) an, falls sie fehlt."""
    client.ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    client.ENV_FILE.touch()
    client.ENV_FILE.chmod(0o600)


async def _request(kennung: str) -> int:
    if client.token():
        print(f"In {client.ENV_FILE} ist schon ein Token gesetzt. Zum Ersetzen erst TUMONLINE_TOKEN leeren.")
        return 1
    root = await client.call(
        "requestToken", needs_token=False, pUsername=kennung, pTokenName=TOKEN_NAME
    )
    new_token = (root.text or "").strip()
    if root.tag != "token" or not new_token:
        print("Unerwartete Antwort von TUMonline, kein Token erhalten.")
        return 1
    ensure_env_file()
    set_key(str(client.ENV_FILE), "TUMONLINE_TOKEN", new_token, quote_mode="never")
    print(f"Neuer Token '{TOKEN_NAME}' angefordert und in {client.ENV_FILE} gespeichert (wird nicht angezeigt).")
    print("Jetzt aktivieren: TUMonline → Visitenkarte → Token-Management → Token")
    print(f"'{TOKEN_NAME}' aktivieren und Rechte für Kalender, Lehrveranstaltungen und Noten freigeben.")
    print("Danach prüfen mit:  tumonline-token --check")
    return 0


async def _check() -> int:
    root = await client.call("isTokenConfirmed")
    ok = (root.text or "").strip().lower() == "true"
    print("Token ist aktiviert." if ok else "Token ist noch NICHT aktiviert (Token-Management in TUMonline).")
    return 0 if ok else 2


def main() -> None:
    p = argparse.ArgumentParser(prog="tumonline-token", description=__doc__.splitlines()[0])
    p.add_argument("kennung", nargs="?", help="TUM-Kennung, z. B. ab12cde")
    p.add_argument("--check", action="store_true", help="nur prüfen, ob der Token aktiviert ist")
    args = p.parse_args()
    if not args.check and not args.kennung:
        p.error("TUM-Kennung angeben oder --check verwenden")
    try:
        sys.exit(asyncio.run(_check() if args.check else _request(args.kennung)))
    except client.TUMonlineError as e:
        print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()


MOODLE_HELP = """\
Moodle-Token einrichten (wird verdeckt eingegeben und nicht angezeigt).

Token finden: https://www.moodle.tum.de → Profilbild → Einstellungen → Sicherheitsschlüssel
→ Schlüssel beim Dienst 'Moodle mobile web service' kopieren.
Alternativ geht auch eine komplette 'moodlemobile://token=…'-Adresse.
"""


def _extract_moodle_token(value: str) -> str:
    """Nimmt einen nackten Token oder eine moodlemobile://token=<base64>-URL entgegen."""
    value = value.strip()
    if "token=" in value:
        import base64

        encoded = value.split("token=", 1)[1].split("&", 1)[0]
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        return decoded.split(":::")[1]  # Format: signatur:::token[:::privattoken]
    return value


async def _check_moodle() -> int:
    from . import moodle

    info = await moodle.site_info()
    print(f"Moodle-Token funktioniert (angemeldet als {info.get('fullname', '?')}).")
    return 0


def moodle_main() -> None:
    import getpass

    p = argparse.ArgumentParser(prog="moodle-token", description=MOODLE_HELP,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="nur prüfen, ob der gespeicherte Token funktioniert")
    args = p.parse_args()
    try:
        if not args.check:
            print(MOODLE_HELP)
            value = _extract_moodle_token(getpass.getpass("Moodle-Token (Eingabe unsichtbar): "))
            if not value:
                print("Nichts eingegeben, abgebrochen.")
                sys.exit(1)
            ensure_env_file()
            set_key(str(client.ENV_FILE), "MOODLE_TOKEN", value, quote_mode="never")
            print(f"Gespeichert in {client.ENV_FILE}.")
        sys.exit(asyncio.run(_check_moodle()))
    except Exception as e:  # noqa: BLE001 - verständliche Meldung statt Traceback
        print(e)
        sys.exit(1)
