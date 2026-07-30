"""Gepäck-Audit: Was ist im aktuell besten Angebot wirklich inkludiert?

Die normale Suchantwort enthält keinerlei Gepäckinformation (geprüft 30.07.2026 —
die einzigen "kg"-Angaben sind CO2-Schätzungen). Erst die Buchungsoptionen eines
konkreten Angebots weisen Gepäckkonditionen aus. Der Weg dorthin kostet drei
Calls, deshalb läuft das Audit nur wöchentlich und nur für das beste Datumspaar:

    1. Suche (liefert Hinflüge mit departure_token)
    2. Suche + departure_token (liefert Rückflüge mit booking_token)
    3. Suche + booking_token (liefert booking_options mit Gepäckangaben)

Ergebnis landet als JSON unter data/baggage_audit/ und wird von Dashboard und
Tages-Digest angezeigt. Aufruf einzeln: python audit.py
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

import requests

import store

AUDIT_DIR = store.BASE / "baggage_audit"
ENDPOINT = "https://serpapi.com/search"
TIMEOUT = 120

# Felder, in denen SerpApi je nach Antwortvariante Gepäckangaben ablegt.
_BAG_KEYS = ("baggage_prices", "extensions")
_BAG_WORDS = ("bag", "gepäck", "gepaeck", "luggage", "koffer")


def _base_params(cfg: dict, api_key: str, pair: tuple[str, str]) -> dict:
    route = cfg["route"]
    params = {
        "engine": "google_flights",
        "api_key": api_key,
        "departure_id": route["origin"],
        "arrival_id": route["destination"],
        "outbound_date": pair[0],
        "return_date": pair[1],
        "type": "1",
        "adults": str(cfg["passenger_configs"]["primary"]),
        "travel_class": str(route.get("travel_class", 1)),
        "currency": "EUR",
        "hl": "de",
        "gl": "de",
    }
    if route.get("bags_per_person"):
        params["bags"] = str(route["bags_per_person"])
    return params


def _get(params: dict) -> tuple[dict | None, str | None]:
    try:
        r = requests.get(ENDPOINT, params=params, timeout=TIMEOUT)
        payload = r.json()
    except requests.RequestException as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if r.status_code != 200 or payload.get("error"):
        return None, payload.get("error") or f"HTTP {r.status_code}"
    return payload, None


def _offers(payload: dict) -> list[dict]:
    return (payload.get("best_flights") or []) + (payload.get("other_flights") or [])


def _cheapest_with(payload: dict, token_key: str) -> dict | None:
    priced = [
        o for o in _offers(payload) if o.get("price") is not None and o.get(token_key)
    ]
    return min(priced, key=lambda o: o["price"]) if priced else None


def _airlines(offer: dict) -> str:
    return ", ".join(
        sorted({l["airline"] for l in (offer.get("flights") or []) if l.get("airline")})
    )


def _bag_lines(option: dict) -> list[str]:
    """Gepäckangaben aus einer Buchungsoption ziehen — defensiv, weil die
    Struktur je nach Anbieter variiert (together/departing/returning)."""
    lines: list[str] = []
    for part in ("together", "departing", "returning"):
        section = option.get(part)
        if not isinstance(section, dict):
            continue
        for key in _BAG_KEYS:
            for entry in section.get(key) or []:
                text = str(entry)
                if key == "baggage_prices" or any(
                    w in text.lower() for w in _BAG_WORDS
                ):
                    lines.append(text)
    return lines


def best_pair(cfg: dict) -> tuple[str, str]:
    """Datumspaar mit dem aktuell günstigsten 5er-Preis, Fallback erstes Kern-Paar."""
    snaps = store.read_snapshots()
    primary = cfg["passenger_configs"]["primary"]
    win_out = set(cfg["dates"]["outbound"])
    win_in = set(cfg["dates"]["inbound"])
    ok = snaps[
        (snaps["status"] == "ok")
        & (snaps["adults"] == primary)
        & snaps["price_min_eur"].notna()
        & snaps["outbound_date"].isin(win_out)
        & snaps["return_date"].isin(win_in)
    ]
    if ok.empty:
        return tuple(cfg["dates"]["core"][0])
    latest = ok.sort_values("ts_utc").groupby(["outbound_date", "return_date"]).last()
    best = latest["price_min_eur"].idxmin()
    return (best[0], best[1])


def run(cfg: dict, api_key: str) -> tuple[int, dict | None]:
    """Führt das Audit aus. Rückgabe: (verbrauchte Calls, Ergebnis oder None)."""
    pair = best_pair(cfg)
    base = _base_params(cfg, api_key, pair)
    calls = 0

    payload, err = _get(base)
    calls += 1
    if err:
        print(f"  [audit] Suche fehlgeschlagen: {err}")
        return calls, None
    outbound = _cheapest_with(payload, "departure_token")
    if outbound is None:
        print("  [audit] kein Angebot mit departure_token")
        return calls, None

    payload2, err = _get({**base, "departure_token": outbound["departure_token"]})
    calls += 1
    if err:
        print(f"  [audit] Rückflug-Abruf fehlgeschlagen: {err}")
        return calls, None
    returning = _cheapest_with(payload2, "booking_token")
    if returning is None:
        print("  [audit] kein Rückflug mit booking_token")
        return calls, None

    payload3, err = _get({**base, "booking_token": returning["booking_token"]})
    calls += 1
    if err:
        print(f"  [audit] Buchungsoptionen fehlgeschlagen: {err}")
        return calls, None

    options = []
    for opt in payload3.get("booking_options") or []:
        merged = opt.get("together") or opt
        options.append(
            {
                "book_with": merged.get("book_with"),
                "price": merged.get("price"),
                "baggage": _bag_lines(opt),
            }
        )

    result = {
        "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pair": {"outbound": pair[0], "return": pair[1]},
        "adults": cfg["passenger_configs"]["primary"],
        "bags_requested": cfg["route"].get("bags_per_person", 0),
        "itinerary": {
            "airlines": _airlines(outbound),
            "price_search": outbound.get("price"),
            "price_booking": returning.get("price"),
        },
        "booking_options": options,
        "api_calls": calls,
    }

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = AUDIT_DIR / f"audit-{day}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    print(
        f"  [audit] {pair[0]} -> {pair[1]}, {result['itinerary']['airlines']}, "
        f"{result['itinerary']['price_booking']} EUR, "
        f"{len(options)} Buchungsoption(en) -> {path.relative_to(store.BASE.parent)}"
    )
    for opt in options[:3]:
        bag = "; ".join(opt["baggage"]) or "keine Gepäckangabe"
        print(f"           {opt['book_with']}: {opt['price']} EUR — {bag}")
    return calls, result


def latest() -> dict | None:
    """Jüngstes Audit-Ergebnis, für Dashboard und Digest."""
    files = sorted(AUDIT_DIR.glob("audit-*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text())


if __name__ == "__main__":
    import os

    import yaml
    from dotenv import load_dotenv

    root = pathlib.Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    config = yaml.safe_load((root / "config.yaml").read_text())
    used, res = run(config, os.environ["SERPAPI_KEY"])
    print(f"\n{used} Calls verbraucht.")
