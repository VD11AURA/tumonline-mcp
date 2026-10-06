"""Moodle über die TUM-Login-Sitzung: Login-Formulare, Seiten-Parser, Umbau ins Webservice-Format."""

import asyncio
import json

import httpx
import pytest

from tumonline_mcp import moodle, moodle_session, moodle_web

LS_PAGE = """
<form name="form1" action="/idp/profile/SAML2/Redirect/SSO?execution=e1s1" method="post">
<input type="hidden" name="csrf_token" value="_abc" />
<input name="shib_idp_ls_success.shib_idp_session_ss" type="hidden" value="false" />
<input name="shib_idp_ls_supported" type="hidden" />
<input name="_eventId_proceed" type="hidden" />
</form>"""

LOGIN_PAGE = """
<form action="/idp/profile/SAML2/Redirect/SSO?execution=e1s2" method="post">
<input type="hidden" name="csrf_token" value="_def" />
<input id="username" name="j_username" type="text" value="">
<input id="password" name="j_password" type="password">
<input type="checkbox" name="donotcache-dummy" value="1" disabled="disabled" />
<input type="hidden" name="donotcache" value="1" />
<button type="submit" name="_eventId_proceed">Login</button>
<button type="submit" name="_eventId_passkey">Passkey</button>
</form>"""

SAML_PAGE = """
<form action="https://www.moodle.tum.de/Shibboleth.sso/SAML2/POST" method="post">
<input type="hidden" name="RelayState" value="cookie:123"/>
<input type="hidden" name="SAMLResponse" value="PHNhbWw+"/>
</form>"""


def test_forms_are_parsed_and_picked():
    ls = moodle_session._pick_form(moodle_session.parse_forms(LS_PAGE))
    assert "shib_idp_ls_supported" in ls["fields"]
    login = moodle_session._pick_form(moodle_session.parse_forms(LOGIN_PAGE))
    assert login["fields"]["csrf_token"] == "_def"
    assert "donotcache-dummy" not in login["fields"]  # nicht angehakte Checkbox
    saml = moodle_session._pick_form(moodle_session.parse_forms(SAML_PAGE + LOGIN_PAGE))
    assert saml["fields"]["SAMLResponse"] == "PHNhbWw+"


def test_sesskey_and_userid_from_moodle_page():
    page = '<script>M.cfg = {"wwwroot":"https:\\/\\/x","sesskey":"Ab12Cd34","sessiontimeout":"28800"};</script>' \
           '<div class="usermenu" data-userid="123456"></div>'
    assert moodle_session.extract_sesskey(page) == "Ab12Cd34"
    assert moodle_session.extract_userid(page) == 123456


def test_full_login_flow_against_fake_idp(monkeypatch):
    """Ablauf LocalStorage-Seite → Login → SAML-Antwort → Moodle mit sesskey."""
    posted = {}

    def handler(request: httpx.Request):
        url = str(request.url)
        if "Shibboleth.sso/Login" in url:
            return httpx.Response(302, headers={"Location": "https://login.tum.de/idp/profile/SAML2/Redirect/SSO?execution=e1s1"})
        if url.endswith("execution=e1s1") and request.method == "POST":
            return httpx.Response(200, text=LOGIN_PAGE)
        if url.endswith("execution=e1s1"):
            return httpx.Response(200, text=LS_PAGE)
        if url.endswith("execution=e1s2"):
            posted.update(dict(x.split("=", 1) for x in request.content.decode().split("&")))
            return httpx.Response(200, text=SAML_PAGE)
        if url.endswith("SAML2/POST"):
            return httpx.Response(302, headers={"Location": "https://www.moodle.tum.de/my/",
                                                "Set-Cookie": "MoodleSession=s3cr3t; path=/"})
        if url.endswith("/my/"):
            return httpx.Response(200, text='"sesskey":"KEY1" data-userid="7"')
        return httpx.Response(404)

    monkeypatch.setattr(moodle_session, "_client", lambda cookies=None: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True, cookies=cookies))
    s = asyncio.run(moodle_session.login("ab12cde", "pw"))
    assert (s.sesskey, s.userid, s.cookies.get("MoodleSession")) == ("KEY1", 7, "s3cr3t")
    assert posted["j_username"] == "ab12cde" and "_eventId_passkey" not in posted


def test_wrong_password_is_reported(monkeypatch):
    error_page = LOGIN_PAGE + '<p class="form-element form-error">Benutzername oder Passwort falsch</p>'

    def handler(request):
        if "Shibboleth.sso/Login" in str(request.url):
            return httpx.Response(200, text=LOGIN_PAGE)
        return httpx.Response(200, text=error_page)

    monkeypatch.setattr(moodle_session, "_client", lambda cookies=None: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True))
    with pytest.raises(moodle_session.MoodleLoginError, match="Passwort falsch"):
        asyncio.run(moodle_session.login("ab12cde", "falsch"))


def test_folder_links_and_file_entries():
    page = ('<a href="https://www.moodle.tum.de/pluginfile.php/55/mod_folder/content/3/Woche%201/blatt.pdf?forcedownload=1">x</a>'
            '<a href="https://www.moodle.tum.de/pluginfile.php/55/mod_folder/content/3/Woche%201/blatt.pdf?forcedownload=1">y</a>'
            '<a href="https://www.moodle.tum.de/pluginfile.php/55/mod_folder/content/3/top.pdf?forcedownload=1">z</a>')
    links = moodle_web.folder_file_links(page)
    assert len(links) == 2
    entry = moodle_web._file_entry(links[0], {"content-length": "1234", "last-modified": "Tue, 06 Oct 2026 10:00:00 GMT"})
    assert entry["filename"] == "blatt.pdf" and entry["filepath"] == "/Woche 1/"
    assert entry["filesize"] == 1234 and entry["timemodified"] > 0
    assert "?" not in entry["fileurl"]
    top = moodle_web._file_entry("https://m/pluginfile.php/9/mod_resource/content/2/Skript%20Kap1.pdf", {})
    assert (top["filename"], top["filepath"]) == ("Skript Kap1.pdf", "/")


STATE = {
    "section": [
        {"id": "1", "title": "Allgemein", "cmlist": ["10", "11"], "number": 0},
        {"id": "2", "title": "Übungen", "cmlist": ["12", "13"], "number": 1},
    ],
    "cm": [
        {"id": "10", "name": "Ankündigungen", "module": "forum", "uservisible": True, "sectionnumber": 0},
        {"id": "11", "name": "Diskussionsforum", "module": "forum", "uservisible": True, "sectionnumber": 0},
        {"id": "12", "name": "Blatt 1", "module": "resource", "uservisible": True, "sectionnumber": 1},
        {"id": "13", "name": "Lösungen &amp; mehr", "module": "folder", "uservisible": True, "sectionnumber": 1},
    ],
}


@pytest.fixture
def session_moodle(tmp_path, monkeypatch):
    moodle_web._cache.clear()
    monkeypatch.setattr(moodle_web, "FILES_CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(moodle, "uses_session", lambda: True)
    calls = {"head": 0, "pages": []}

    async def ajax(function, args=None):
        if function == "core_courseformat_get_state":
            return json.dumps(STATE)
        if function == "mod_forum_get_discussion_posts":
            return {"posts": [{"subject": f"News {args['discussionid']}", "message": "<p>Hallo</p>",
                               "author": {"fullname": "Prof. X"}, "timecreated": 4102444800}]}
        raise AssertionError(function)

    async def head(path, params=None):
        calls["head"] += 1
        if "resource" in path:
            return "https://www.moodle.tum.de/pluginfile.php/5/mod_resource/content/1/blatt1.pdf", {"content-length": "10"}
        return path, {"content-length": "20"}

    async def get_page(path, params=None):
        calls["pages"].append(path)
        if "folder" in path:
            return '<a href="https://www.moodle.tum.de/pluginfile.php/6/mod_folder/content/1/l1.pdf?forcedownload=1">', ""
        if "forum" in path:
            return '<tr class="discussion pinned" data-discussionid="501"></tr><tr data-discussionid="502"></tr>', ""
        raise AssertionError(path)

    monkeypatch.setattr(moodle_session, "ajax", ajax)
    monkeypatch.setattr(moodle_session, "head", head)
    monkeypatch.setattr(moodle_session, "get_page", get_page)
    return calls


def test_course_contents_in_webservice_format(session_moodle):
    contents = asyncio.run(moodle.contents(5))
    assert [s["name"] for s in contents] == ["Allgemein", "Übungen"]
    blatt, folder = contents[1]["modules"]
    assert blatt["contents"][0]["filename"] == "blatt1.pdf"
    assert folder["name"] == "Lösungen & mehr" and folder["contents"][0]["filename"] == "l1.pdf"
    # zweiter Aufruf aus dem Datei-Cache: keine erneuten Seitenaufrufe
    heads = session_moodle["head"]
    asyncio.run(moodle.contents(5))
    assert session_moodle["head"] == heads


def test_course_contents_tool_does_not_resolve_files(session_moodle):
    sections = asyncio.run(moodle.course_contents(5))
    assert session_moodle["head"] == 0 and session_moodle["pages"] == []
    assert any(m["name"] == "Blatt 1" for s in sections for m in s["inhalte"])


def test_announcements_from_news_forum_only(session_moodle):
    posts = asyncio.run(moodle.announcements([5], days=30))
    assert [p["id"] for p in posts] == [501, 502]
    assert posts[0]["angeheftet"] and posts[0]["von"] == "Prof. X" and posts[0]["text"] == "Hallo"
    assert session_moodle["pages"] == ["/mod/forum/view.php"]  # Diskussionsforum ignoriert


GRADE_REPORT = """
<table class="generaltable user-grade">
<tr><th class="header column-itemname">Bewertungsaspekt</th><th class="header column-grade">Bewertung</th></tr>
<tr class=""><th class="level1 levelodd oddd1 b1b b1t column-itemname" colspan="5">Analysis 1</th></tr>
<tr><th class="level2 leveleven item b1b column-itemname"><a href="https://www.moodle.tum.de/mod/assign/view.php?id=1" class="gradeitemheader">Blatt 1</a></th>
<td class="level2 leveleven item b1b itemcenter column-grade">7,50</td><td class="column-range">0,00&ndash;10,00</td><td class="column-percentage">75,00 %</td></tr>
<tr><th class="level2 leveleven item b1b column-itemname"><a href="https://www.moodle.tum.de/mod/assign/view.php?id=2" class="gradeitemheader">Blatt 2</a></th>
<td class="level2 leveleven item b1b itemcenter column-grade">-</td><td class="column-range">0,00&ndash;10,00</td><td class="column-percentage">-</td></tr>
<tr><th class="level1 levelodd item b1b column-itemname"><span class="gradeitemheader">Kurs gesamt</span></th>
<td class="level1 levelodd item b1b itemcenter column-grade">7,50</td><td class="column-range">0,00&ndash;20,00</td><td class="column-percentage">37,50 %</td></tr>
</table>"""


def test_grade_report_is_parsed(monkeypatch):
    async def get_page(path, params=None):
        return GRADE_REPORT, ""

    monkeypatch.setattr(moodle, "uses_session", lambda: True)
    monkeypatch.setattr(moodle_session, "get_page", get_page)
    g = asyncio.run(moodle.grades(5))
    assert [b["name"] for b in g["bewertungen"]] == ["Blatt 1", "Blatt 2"]
    assert g["bewertungen"][0]["punkte"] == 7.5 and g["bewertungen"][0]["max"] == 10
    assert g["bewertet"] == 1 and g["summe_punkte"] == 7.5 and g["summe_max_alle"] == 20
    assert g["kursgesamt"]["anzeige"] == "7,50"
