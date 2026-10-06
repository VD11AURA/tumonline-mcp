"""TUM-Moodle ohne Mobile-Token: Anmeldung über TUM-SSO (Shibboleth) wie ein Browser.

TUM-Moodle hat den "Moodle mobile web service" abgeschaltet, Tokens gibt es dort nicht mehr.
Stattdessen meldet sich dieses Modul mit TUM-Kennung und Passwort an (Passwort im Schlüsselbund
des Betriebssystems, nicht in der .env) und nutzt die Schnittstellen der Moodle-Weboberfläche:
lib/ajax/service.php (JSON, mit sesskey) und normale Seiten. Läuft die Sitzung ab, wird sie
automatisch erneuert.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from .client import CONFIG_DIR, setting
from .http import ServiceError

KEYRING_SERVICE = "tumonline-mcp-moodle"
SESSION_FILE = CONFIG_DIR / ".state" / "moodle_session.json"
IDP_PROVIDER = "https://tumidp.lrz.de/idp/shibboleth"
MAX_LOGIN_STEPS = 10
# Diese Fehlercodes bedeuten: Sitzung abgelaufen, einmal neu anmelden und wiederholen.
SESSION_ERRORS = {"servicerequireslogin", "invalidsesskey", "requireloginerror", "sessiontimedout"}


class MoodleLoginError(ServiceError):
    pass


def base_url() -> str:
    return setting("MOODLE_BASE_URL", "https://www.moodle.tum.de").rstrip("/")


def username() -> str:
    return setting("MOODLE_USERNAME")


def _keyring():
    import keyring

    return keyring


def password(user: str) -> str | None:
    return _keyring().get_password(KEYRING_SERVICE, user) if user else None


def store_credentials(user: str, pw: str) -> None:
    _keyring().set_password(KEYRING_SERVICE, user, pw)


def delete_credentials(user: str) -> None:
    try:
        _keyring().delete_password(KEYRING_SERVICE, user)
    except Exception:  # noqa: BLE001 - nichts gespeichert ist auch ok
        pass


def is_configured() -> bool:
    return bool(username())


# --- HTML-Formulare -------------------------------------------------------


class _FormParser(HTMLParser):
    """Sammelt Formulare mit ihren Eingabefeldern (action, method, {name: value})."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.forms.append({"action": a.get("action") or "", "method": (a.get("method") or "get").lower(),
                               "fields": {}})
        elif tag in ("input", "button") and self.forms and a.get("name"):
            if a.get("type") in ("checkbox", "radio") and "checked" not in a:
                return
            self.forms[-1]["fields"].setdefault(a["name"], a.get("value") or "")


def parse_forms(page: str) -> list[dict]:
    parser = _FormParser()
    parser.feed(page)
    return parser.forms


def _login_error(page: str) -> str:
    m = re.search(r'class="[^"]*form-error[^"]*"[^>]*>(.*?)</', page, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""


def extract_sesskey(page: str) -> str:
    m = re.search(r'"sesskey":"([^"]+)"', page)
    return m.group(1) if m else ""


def extract_userid(page: str) -> int | None:
    m = re.search(r'data-userid="(\d+)"', page) or re.search(r"/user/profile\.php\?id=(\d+)", page)
    return int(m.group(1)) if m else None


def _on_moodle(url: httpx.URL) -> bool:
    return url.host == urlparse(base_url()).hostname and "/login/" not in url.path


# --- Sitzung --------------------------------------------------------------


class Session:
    def __init__(self, cookies: dict, sesskey: str, userid: int | None):
        self.cookies, self.sesskey, self.userid = cookies, sesskey, userid

    def save(self) -> None:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps(
            {"cookies": self.cookies, "sesskey": self.sesskey, "userid": self.userid}), encoding="utf-8")
        if os.name != "nt":
            SESSION_FILE.chmod(0o600)

    @classmethod
    def load(cls) -> Session | None:
        try:
            d = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            return cls(d["cookies"], d["sesskey"], d.get("userid"))
        except (OSError, ValueError, KeyError):
            return None


def _client(cookies: dict | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30, follow_redirects=True, cookies=cookies,
                             headers={"User-Agent": "Mozilla/5.0 (tumonline-mcp)"})


async def login(user: str, pw: str) -> Session:
    """Durchläuft den TUM-SSO-Ablauf (Shibboleth/SAML) und gibt eine Moodle-Sitzung zurück."""
    target = f"{base_url()}/auth/shibboleth/index.php"
    start = f"{base_url()}/Shibboleth.sso/Login"
    sent_credentials = False
    try:
        async with _client() as http:
            resp = await http.get(start, params={"providerId": IDP_PROVIDER, "target": target})
            for _ in range(MAX_LOGIN_STEPS):
                resp.raise_for_status()
                page = resp.text
                if _on_moodle(resp.url) and (sesskey := extract_sesskey(page)):
                    moodle_host = urlparse(base_url()).hostname
                    cookies = {c.name: c.value for c in http.cookies.jar if moodle_host.endswith(c.domain.lstrip("."))}
                    return Session(cookies, sesskey, extract_userid(page))
                form = _pick_form(parse_forms(page))
                if form is None:
                    raise MoodleLoginError(
                        f"Moodle-Anmeldung: unerwartete Seite ({resp.url.host}{resp.url.path}). "
                        "Falls TUM eine Zwei-Faktor-Abfrage verlangt, geht der automatische Login nicht."
                    )
                fields = dict(form["fields"])
                if "j_username" in fields:
                    if sent_credentials:
                        raise MoodleLoginError(
                            f"TUM-Login abgelehnt: {_login_error(page) or 'Kennung oder Passwort falsch.'}")
                    fields.update(j_username=user, j_password=pw, _eventId_proceed="")
                    fields.pop("_eventId_passkey", None)
                    sent_credentials = True
                elif any(k.startswith("shib_idp_ls_") for k in fields):
                    fields.update({k: "false" for k in fields if k.startswith("shib_idp_ls_success")})
                    fields["shib_idp_ls_supported"] = "false"
                action = urljoin(str(resp.url), form["action"]) if form["action"] else str(resp.url)
                if form["method"] == "post":
                    resp = await http.post(action, data=fields)
                else:
                    resp = await http.get(action, params=fields)
    except httpx.HTTPError as e:
        raise MoodleLoginError(f"Moodle-Anmeldung fehlgeschlagen ({type(e).__name__})") from e
    raise MoodleLoginError("Moodle-Anmeldung: zu viele Weiterleitungen, abgebrochen.")


def _pick_form(forms: list[dict]) -> dict | None:
    """Das Formular, das den Login-Ablauf weiterbringt (SAML-Antwort, Login, Einwilligung)."""
    for key in ("SAMLResponse", "j_username", "shib_idp_ls_supported", "_eventId_proceed"):
        for f in forms:
            if key in f["fields"]:
                return f
    return None


_session: Session | None = None
_lock = asyncio.Lock()


async def session(renew: bool = False) -> Session:
    """Gespeicherte Sitzung oder (bei renew bzw. ohne Sitzung) neue Anmeldung."""
    global _session
    async with _lock:
        if not renew:
            _session = _session or Session.load()
            if _session:
                return _session
        user = username()
        pw = password(user)
        if not user or not pw:
            raise MoodleLoginError(
                "Moodle ist nicht eingerichtet. In einem Terminal `moodle-login` ausführen "
                "(TUM-Kennung und Passwort, das Passwort landet im Windows-/macOS-Schlüsselbund).")
        _session = await login(user, pw)
        _session.save()
        return _session


def _expired(resp: httpx.Response) -> bool:
    return not _on_moodle(resp.url) or "login.tum.de" in str(resp.url)


async def ajax(function: str, args: dict | None = None):
    """Ruft eine Moodle-Funktion über lib/ajax/service.php auf (wie die Weboberfläche)."""
    for attempt in range(2):
        s = await session(renew=attempt > 0)
        try:
            async with _client(s.cookies) as http:
                resp = await http.post(
                    f"{base_url()}/lib/ajax/service.php",
                    params={"sesskey": s.sesskey, "info": function},
                    content=json.dumps([{"index": 0, "methodname": function, "args": args or {}}]),
                    headers={"Content-Type": "application/json"},
                )
            resp.raise_for_status()
        except httpx.TooManyRedirects:
            if attempt == 0:
                continue
            raise ServiceError(f"Moodle ({function}): Anmeldung erneuert, trotzdem abgelehnt.") from None
        except httpx.HTTPError as e:
            raise ServiceError(f"Moodle ({function}) nicht erreichbar ({type(e).__name__})") from e
        try:
            data = resp.json()
        except ValueError:
            if attempt == 0 and _expired(resp):
                continue
            raise ServiceError(f"Moodle ({function}): unerwartete Antwort") from None
        result = data[0] if isinstance(data, list) and data else data
        if isinstance(result, dict) and result.get("error"):
            exc = result.get("exception") or result
            code = exc.get("errorcode", "") if isinstance(exc, dict) else ""
            if attempt == 0 and code in SESSION_ERRORS:
                continue
            msg = exc.get("message", code) if isinstance(exc, dict) else str(exc)
            raise ServiceError(f"Moodle-Fehler ({function}): {msg}")
        return result.get("data") if isinstance(result, dict) else result
    raise ServiceError(f"Moodle ({function}): Anmeldung erneuert, trotzdem abgelehnt.")


async def _fetch(method: str, path: str, params: dict | None) -> httpx.Response:
    """GET/HEAD mit der Sitzung; bei abgelaufener Sitzung einmal neu anmelden und wiederholen."""
    url = path if path.startswith("http") else urljoin(base_url() + "/", path.lstrip("/"))
    for attempt in range(2):
        s = await session(renew=attempt > 0)
        try:
            async with _client(s.cookies) as http:
                resp = await http.request(method, url, params=params)
            resp.raise_for_status()
        except httpx.TooManyRedirects:
            # Moodle leitet mit abgelaufener Sitzung teils im Kreis statt zum Login.
            if attempt == 0:
                continue
            raise ServiceError(f"Moodle ({path}): Anmeldung erneuert, trotzdem abgelehnt.") from None
        except httpx.HTTPStatusError as e:
            raise ServiceError(f"Moodle ({path}): HTTP {e.response.status_code} "
                               "(kein Zugriff oder nicht eingeschrieben?)") from e
        except httpx.HTTPError as e:
            raise ServiceError(f"Moodle ({path}) nicht erreichbar ({type(e).__name__})") from e
        if attempt == 0 and _expired(resp):
            continue
        return resp
    raise ServiceError(f"Moodle ({path}): Anmeldung erneuert, trotzdem abgelehnt.")


async def get_page(path: str, params: dict | None = None) -> tuple[str, str]:
    """Lädt eine Moodle-Seite mit der Sitzung. Gibt (HTML, End-URL) zurück."""
    resp = await _fetch("GET", path, params)
    return resp.text, str(resp.url)


async def head(path: str, params: dict | None = None) -> tuple[str, httpx.Headers]:
    """HEAD mit Weiterleitungen: End-URL und Header (Dateiname, Größe, Änderungsdatum)."""
    resp = await _fetch("HEAD", path, params)
    return str(resp.url), resp.headers


def session_file_url(fileurl: str) -> str:
    """Webservice-Dateilinks (Token) auf normale Browser-Links (Sitzung) umbiegen."""
    return fileurl.replace("/webservice/pluginfile.php", "/pluginfile.php")


async def download(url: str, dest: Path, *, label: str = "Moodle-Datei") -> int:
    tmp = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        s = await session(renew=attempt > 0)
        size = 0
        try:
            async with _client(s.cookies) as http:
                async with http.stream("GET", session_file_url(url)) as resp:
                    resp.raise_for_status()
                    if _expired(resp):
                        if attempt == 0:
                            continue
                        raise ServiceError(f"{label}: nicht angemeldet")
                    with open(tmp, "wb") as f:
                        async for chunk in resp.aiter_bytes():
                            f.write(chunk)
                            size += len(chunk)
        except httpx.TooManyRedirects:
            tmp.unlink(missing_ok=True)
            if attempt == 0:
                continue
            raise ServiceError(f"{label}: nicht angemeldet") from None
        except httpx.HTTPError as e:
            tmp.unlink(missing_ok=True)
            raise ServiceError(f"{label} fehlgeschlagen ({type(e).__name__})") from e
        tmp.replace(dest)
        return size
    raise ServiceError(f"{label}: Anmeldung erneuert, trotzdem abgelehnt.")
