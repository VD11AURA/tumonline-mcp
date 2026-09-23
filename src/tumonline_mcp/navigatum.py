"""Raumsuche über NavigaTUM (nav.tum.de), öffentlich, kein Token nötig."""

from __future__ import annotations

import re
from urllib.parse import quote

from .http import ServiceError, get_json

API = "https://nav.tum.de/api"
CACHE_SECONDS = 24 * 3600

# TUMonline-Ortsangaben enden mit der Raum-ID in Klammern: "…, Seminarraum (5608.02.011)"
ROOM_ID = re.compile(r"\((\d{4}\.[A-Z0-9]{2}\.[0-9A-Z]+)\)\s*$")


def room_id_from_location(location: str) -> str | None:
    m = ROOM_ID.search(location or "")
    return m.group(1) if m else None


def map_url(room_id: str) -> str:
    return f"https://nav.tum.de/room/{quote(room_id)}"


def _computed(props: dict, name: str) -> str:
    for item in props.get("computed", []):
        if item.get("name") == name:
            return item.get("text", "")
    return ""


async def location(room_id: str) -> dict:
    data = await get_json(f"{API}/locations/{quote(room_id)}", ttl=CACHE_SECONDS, label="NavigaTUM")
    props = data.get("props", {})
    coords = data.get("coords") or {}
    out = {
        "id": data.get("id", room_id),
        "name": data.get("name", ""),
        "typ": data.get("type_common_name", ""),
        "gebaeude": " › ".join(data.get("parent_names", [])[1:]),
        "adresse": _computed(props, "Adresse"),
        "stockwerk": _computed(props, "Stockwerk"),
        "karte": f"https://nav.tum.de{data.get('redirect_url') or '/room/' + quote(room_id)}",
    }
    if coords.get("lat") and coords.get("lon"):
        out["google_maps"] = f"https://www.google.com/maps/search/?api=1&query={coords['lat']},{coords['lon']}"
    return out


async def search(query: str, limit: int = 5) -> list[dict]:
    data = await get_json(f"{API}/search", {"q": query, "limit_all": limit}, ttl=CACHE_SECONDS, label="NavigaTUM")
    hits = []
    for section in data.get("sections", []):
        if section.get("facet") not in ("rooms", "buildings", "sites"):
            continue
        for e in section.get("entries", []):
            name = re.sub(r"[\x17\x19]", "", e.get("name", ""))  # Hervorhebungs-Steuerzeichen
            hits.append({"id": e.get("id"), "typ": e.get("type"), "name": name, "ort": e.get("subtext", "")})
    return hits[:limit]


async def resolve(query: str) -> dict:
    """Raum-ID, Architektenname (02.04.011@5604) oder Freitext → Details des besten Treffers."""
    try:
        return await location(query)
    except ServiceError:
        pass
    hits = await search(query, limit=3)
    if not hits:
        raise ServiceError(f"Kein Raum/Gebäude zu '{query}' gefunden.")
    best = await location(hits[0]["id"])
    best["weitere_treffer"] = hits[1:]
    return best
