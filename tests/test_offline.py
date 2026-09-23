"""Tests ohne Netzwerk: Parsing, Filter und Hilfsfunktionen."""

import base64
from datetime import date

import pytest

from tumonline_mcp import client, navigatum, server
from tumonline_mcp.token_setup import _extract_moodle_token


def test_records_parses_rowset():
    root = client.parse("x", b"<rowset><row><a>1</a><b> zwei </b></row><row><a>3</a></row></rowset>")
    assert client._records(root) == [{"a": "1", "b": "zwei"}, {"a": "3"}]


def test_error_response_raises_readable_error():
    with pytest.raises(client.TUMonlineError, match="Token ist ungültig"):
        client.parse("kalender", b"<error><message>Token ist ung\xc3\xbcltig!</message></error>")


def test_room_id_from_location():
    assert navigatum.room_id_from_location("02.08.011, Seminarraum (5608.02.011)") == "5608.02.011"
    assert navigatum.room_id_from_location("00.02.001, MI HS 1 (5602.EG.001)") == "5602.EG.001"
    assert navigatum.room_id_from_location("online") is None


def test_range_defaults_and_validation():
    start, end = server._range("2026-10-01", None, 7)
    assert (start, end) == (date(2026, 10, 1), date(2026, 10, 8))
    with pytest.raises(client.TUMonlineError):
        server._range("01.10.2026", None, 7)
    with pytest.raises(client.TUMonlineError):
        server._range("2026-10-08", "2026-10-01", 7)


def test_exam_pattern():
    assert server.EXAM_PATTERN.search("Klausur Analysis 1")
    assert server.EXAM_PATTERN.search("Endterm Exam LA")
    assert not server.EXAM_PATTERN.search("Vorlesung Analysis 1")


def test_event_adds_map_link():
    ev = server._event({"title": "VL", "location": "MI HS 1 (5602.EG.001)", "status": "CANCEL"})
    assert ev["abgesagt"] and ev["karte"] == "https://nav.tum.de/room/5602.EG.001"


def test_extract_moodle_token():
    assert _extract_moodle_token("  abc123  ") == "abc123"
    url = "moodlemobile://token=" + base64.b64encode(b"sig:::tok456:::priv").decode()
    assert _extract_moodle_token(url) == "tok456"


def test_current_semester_and_rank():
    assert server.current_semester(date(2026, 9, 23)) == "26W"
    assert server.current_semester(date(2027, 3, 31)) == "26W"
    assert server.current_semester(date(2027, 4, 1)) == "27S"
    assert server._semester_rank("26W") - server._semester_rank("26S") == 1
    assert server._semester_rank("27S") - server._semester_rank("26W") == 1
    assert server._normalize_semester(" 26w ") == "26W"
    with pytest.raises(client.TUMonlineError):
        server._normalize_semester("WS 2026/27")
