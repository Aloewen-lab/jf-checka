"""M0-Smoke-Test: verifiziert SerpApi-Zugang, Quota und Antwortstruktur.

Prüft insbesondere, ob der Metro-Code TYO (Tokio, alle Flughäfen) als arrival_id
akzeptiert wird — sonst muss auf NRT/HND einzeln ausgewichen werden.

Verbraucht 1 Suche pro getestetem Ziel. Account-Abfrage ist kostenlos.
"""

import json
import os
import pathlib
import sys

import requests
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

API_KEY = os.environ["SERPAPI_KEY"]
SCRATCH = pathlib.Path(__file__).resolve().parent.parent / "scratch"
SCRATCH.mkdir(exist_ok=True)

OUT_DATE = "2027-03-20"
RET_DATE = "2027-04-03"


def account():
    r = requests.get(
        "https://serpapi.com/account", params={"api_key": API_KEY}, timeout=30
    )
    r.raise_for_status()
    return r.json()


def search(arrival_id, adults):
    params = {
        "engine": "google_flights",
        "api_key": API_KEY,
        "departure_id": "BER",
        "arrival_id": arrival_id,
        "outbound_date": OUT_DATE,
        "return_date": RET_DATE,
        "type": "1",  # 1 = Round trip
        "adults": str(adults),
        "travel_class": "1",  # Economy
        "currency": "EUR",
        "hl": "de",
        "gl": "de",
    }
    r = requests.get("https://serpapi.com/search", params=params, timeout=90)
    return r.status_code, r.json()


def summarize(payload):
    """Struktur der Antwort auf das reduzieren, was der Parser später braucht."""
    out = {"top_level_keys": sorted(payload.keys())}

    if "error" in payload:
        out["error"] = payload["error"]
        return out

    for bucket in ("best_flights", "other_flights"):
        items = payload.get(bucket) or []
        out[f"{bucket}_count"] = len(items)

    out["price_insights"] = payload.get("price_insights")

    items = (payload.get("best_flights") or []) + (payload.get("other_flights") or [])
    prices = [i["price"] for i in items if i.get("price") is not None]
    if prices:
        out["price_min"] = min(prices)
        out["price_max"] = max(prices)

    if items:
        cheapest = min(
            (i for i in items if i.get("price") is not None),
            key=lambda i: i["price"],
        )
        legs = cheapest.get("flights", [])
        out["cheapest"] = {
            "price": cheapest.get("price"),
            "type": cheapest.get("type"),
            "total_duration_min": cheapest.get("total_duration"),
            "n_legs": len(legs),
            "airlines": sorted({leg.get("airline") for leg in legs if leg.get("airline")}),
            "route": " > ".join(
                [legs[0]["departure_airport"]["id"]] if legs else []
            )
            + "".join(f" > {leg['arrival_airport']['id']}" for leg in legs),
            "has_booking_token": bool(cheapest.get("booking_token")),
        }
        out["offer_keys"] = sorted(cheapest.keys())
        if legs:
            out["leg_keys"] = sorted(legs[0].keys())

    return out


def main():
    acct = account()
    print("== Account ==")
    for k in ("plan_name", "searches_per_month", "total_searches_left",
              "this_month_usage", "plan_searches_left", "account_rate_limit_per_hour"):
        if k in acct:
            print(f"  {k}: {acct[k]}")

    for arrival in ("TYO", "NRT"):
        print(f"\n== Suche BER -> {arrival}, 3 Erwachsene, {OUT_DATE} / {RET_DATE} ==")
        status, payload = search(arrival, adults=3)
        (SCRATCH / f"raw_{arrival}.json").write_text(json.dumps(payload, indent=2))
        print(f"  HTTP {status}")
        print(json.dumps(summarize(payload), indent=2, ensure_ascii=False))
        if status == 200 and not payload.get("error"):
            print(f"\n  -> {arrival} funktioniert. Rohdaten: scratch/raw_{arrival}.json")
            return 0
        print(f"  -> {arrival} fehlgeschlagen, nächster Versuch.")

    return 1


if __name__ == "__main__":
    sys.exit(main())
