"""Speisepläne der Mensen über die öffentliche TUM Eat-API (tum-dev.github.io/eat-api)."""

from __future__ import annotations

from datetime import date

from .http import ServiceError, get_json

API = "https://tum-dev.github.io/eat-api"
CACHE_SECONDS = 3600

LABEL_DE = {"VEGAN": "vegan", "VEGETARIAN": "vegetarisch", "MEAT": "Fleisch", "PORK": "Schwein",
            "BEEF": "Rind", "POULTRY": "Geflügel", "FISH": "Fisch", "GLUTEN": "Gluten",
            "LACTOSE": "Laktose", "MILK": "Milch", "ALCOHOL": "Alkohol"}


async def canteens() -> list[dict]:
    data = await get_json(f"{API}/enums/canteens.json", ttl=24 * 3600, label="Eat-API")
    return [
        {
            "id": c["canteen_id"],
            "name": c["name"],
            "adresse": (c.get("location") or {}).get("address", ""),
            "oeffnungszeiten": {
                tag: f"{z['start']}–{z['end']}" for tag, z in (c.get("open_hours") or {}).items()
            },
        }
        for c in data
    ]


def _price(prices: dict, group: str) -> str:
    p = prices.get(group) or {}
    base, per_unit, unit = p.get("base_price") or 0, p.get("price_per_unit") or 0, p.get("unit") or ""
    parts = []
    if base:
        parts.append(f"{base:.2f} €")
    if per_unit:
        parts.append(f"{per_unit:.2f} €/{unit}")
    return " + ".join(parts) or "–"


async def menu(canteen_id: str, day: date, nur_vegetarisch: bool = False) -> dict:
    year, week, _ = day.isocalendar()
    try:
        data = await get_json(f"{API}/{canteen_id}/{year}/{week:02d}.json", ttl=CACHE_SECONDS, label="Eat-API")
    except ServiceError as e:
        if "HTTP 404" in str(e):
            known = ", ".join(c["id"] for c in await canteens())
            raise ServiceError(
                f"Kein Speiseplan für '{canteen_id}' in KW {week}/{year}. Bekannte Mensen: {known}"
            ) from e
        raise
    for d in data.get("days", []):
        if d.get("date") == day.isoformat():
            dishes = []
            for dish in d.get("dishes", []):
                labels = dish.get("labels", [])
                if nur_vegetarisch and "VEGETARIAN" not in labels and "VEGAN" not in labels:
                    continue
                dishes.append({
                    "gericht": dish.get("name", ""),
                    "art": dish.get("dish_type", ""),
                    "preis_studierende": _price(dish.get("prices", {}), "students"),
                    "hinweise": [LABEL_DE[x] for x in labels if x in LABEL_DE],
                })
            return {"mensa": canteen_id, "datum": day.isoformat(), "gerichte": dishes}
    return {"mensa": canteen_id, "datum": day.isoformat(), "gerichte": [],
            "hinweis": "An diesem Tag kein Speiseplan (Wochenende, Feiertag oder geschlossen)."}
