"""SerpApi Google Flights — Primärquelle.

Verifizierte Eigenheiten der API (30.07.2026), die den Parser bestimmen:

* Angebote stehen in ZWEI Listen, `best_flights` und `other_flights`. Das Minimum
  kann in `other_flights` liegen, beide müssen gelesen werden.
* `price` ist der Gesamtpreis der Roundtrip-Buchung für ALLE Passagiere.
* Bei `type=1` enthält `flights` nur die Hinflug-Legs — der Preis ist trotzdem der
  volle Roundtrip-Preis. Ein Zweitcall mit `departure_token` ist fürs Monitoring
  unnötig und würde die Calls verdoppeln.
* `arrival_id` akzeptiert KEINE Metro-Codes ("TYO" -> no results). Der Stadt-Code
  `/m/07dfk` liefert dagegen HND und NRT in einem Call.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from models import Offer, PricePoint, SearchRequest, SearchResult, Snapshot

ENDPOINT = "https://serpapi.com/search"
ACCOUNT_ENDPOINT = "https://serpapi.com/account"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 4


class SerpApiProvider:
    name = "serpapi_google_flights"

    def __init__(self, api_key: str, timeout: int = 90):
        self._api_key = api_key
        self._timeout = timeout

    # ------------------------------------------------------------------ quota

    def searches_left(self) -> int | None:
        """Kostenlos — zählt nicht gegen das Kontingent."""
        try:
            r = requests.get(
                ACCOUNT_ENDPOINT, params={"api_key": self._api_key}, timeout=30
            )
            r.raise_for_status()
            return int(r.json()["total_searches_left"])
        except Exception:
            return None

    # ----------------------------------------------------------------- search

    def search(self, req: SearchRequest) -> SearchResult:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        params = {
            "engine": "google_flights",
            "api_key": self._api_key,
            "departure_id": req.origin,
            "arrival_id": req.destination,
            "outbound_date": req.outbound_date,
            "return_date": req.return_date,
            "type": "1",  # Round trip
            "adults": str(req.adults),
            "travel_class": str(req.travel_class),
            "currency": req.currency,
            "hl": "de",
            "gl": "de",
        }
        if req.bags:
            params["bags"] = str(req.bags)

        payload, error, calls = self._get_with_retry(params)

        if error is not None:
            return SearchResult(self._snapshot(ts, req, "error", error=error, api_calls=calls))

        api_error = payload.get("error")
        if api_error:
            # Häufigster Fall: Google liefert für diese Kombination nichts.
            status = "no_results" if "hasn't returned any results" in api_error else "error"
            return SearchResult(
                self._snapshot(ts, req, status, error=api_error, api_calls=calls)
            )

        deep_link = (payload.get("search_metadata") or {}).get("google_flights_url", "")
        offers: list[Offer] = []
        for bucket in ("best_flights", "other_flights"):
            for raw in payload.get(bucket) or []:
                offer = self._parse_offer(ts, req, raw, bucket.split("_")[0], deep_link)
                if offer is not None:
                    offers.append(offer)

        insights = payload.get("price_insights") or {}
        typical = insights.get("typical_price_range") or [None, None]
        prices = [o.price_eur for o in offers]
        history = self._parse_history(ts, req, insights.get("price_history"))

        snap = self._snapshot(
            ts,
            req,
            "ok" if offers else "no_results",
            n_offers=len(offers),
            price_min_eur=min(prices) if prices else None,
            price_level=insights.get("price_level"),
            typical_low_eur=typical[0],
            typical_high_eur=typical[1],
            api_calls=calls,
        )
        return SearchResult(snap, offers, history)

    @staticmethod
    def _parse_history(ts: str, req: SearchRequest, raw: object) -> list[PricePoint]:
        """`price_history` ist eine Liste [unix_timestamp, preis]."""
        if not isinstance(raw, list):
            return []
        points: list[PricePoint] = []
        for entry in raw:
            if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
                continue
            stamp, price = entry
            try:
                day = datetime.fromtimestamp(int(stamp), timezone.utc).date().isoformat()
            except (TypeError, ValueError, OSError):
                continue
            points.append(
                PricePoint(
                    ts_utc=ts,
                    provider=SerpApiProvider.name,
                    outbound_date=req.outbound_date,
                    return_date=req.return_date,
                    adults=req.adults,
                    hist_date=day,
                    price_eur=float(price),
                )
            )
        return points

    # ---------------------------------------------------------------- interna

    def _get_with_retry(self, params: dict) -> tuple[dict | None, str | None, int]:
        """Retry nur bei transienten Fehlern. Jeder Versuch, der Google erreicht,
        zählt gegen das Kontingent — deshalb wird `calls` mitgezählt."""
        calls = 0
        last_error = "unbekannt"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                r = requests.get(ENDPOINT, params=params, timeout=self._timeout)
                calls += 1
                if r.status_code == 200:
                    return r.json(), None, calls
                if r.status_code == 429:
                    last_error = "rate limit (429)"
                elif 500 <= r.status_code < 600:
                    last_error = f"server error ({r.status_code})"
                else:
                    # 4xx außer 429: erneuter Versuch ist sinnlos.
                    return None, f"HTTP {r.status_code}: {r.text[:200]}", calls
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS * attempt)
        return None, last_error, calls

    def _parse_offer(
        self, ts: str, req: SearchRequest, raw: dict, bucket: str, deep_link: str
    ) -> Offer | None:
        price = raw.get("price")
        if price is None:
            return None  # Angebote ohne Preis (selten) sind für uns wertlos.

        legs = raw.get("flights") or []
        layovers = raw.get("layovers") or []
        carbon = (raw.get("carbon_emissions") or {}).get("this_flight")

        return Offer(
            ts_utc=ts,
            provider=self.name,
            params_hash=req.params_hash,
            origin=req.origin,
            destination=req.destination,
            outbound_date=req.outbound_date,
            return_date=req.return_date,
            adults=req.adults,
            bags=req.bags,
            price_eur=float(price),
            price_per_person_eur=round(float(price) / req.adults, 2),
            bucket=bucket,
            arrival_airport=legs[-1]["arrival_airport"]["id"] if legs else "",
            airlines=", ".join(
                sorted({leg["airline"] for leg in legs if leg.get("airline")})
            ),
            stops_out=max(len(legs) - 1, 0),
            duration_out_min=int(raw.get("total_duration") or 0),
            layovers=", ".join(
                f"{lo.get('id')} {lo.get('duration')}min" for lo in layovers
            ),
            departure_time=legs[0]["departure_airport"].get("time", "") if legs else "",
            arrival_time=legs[-1]["arrival_airport"].get("time", "") if legs else "",
            travel_class=legs[0].get("travel_class", "") if legs else "",
            carbon_kg=round(carbon / 1000, 1) if carbon else None,
            deep_link=deep_link,
        )

    @staticmethod
    def _snapshot(
        ts: str,
        req: SearchRequest,
        status: str,
        *,
        n_offers: int = 0,
        price_min_eur: float | None = None,
        price_level: str | None = None,
        typical_low_eur: float | None = None,
        typical_high_eur: float | None = None,
        error: str | None = None,
        api_calls: int = 0,
    ) -> Snapshot:
        return Snapshot(
            ts_utc=ts,
            provider=SerpApiProvider.name,
            params_hash=req.params_hash,
            origin=req.origin,
            destination=req.destination,
            outbound_date=req.outbound_date,
            return_date=req.return_date,
            adults=req.adults,
            bags=req.bags,
            status=status,
            n_offers=n_offers,
            price_min_eur=price_min_eur,
            price_level=price_level,
            typical_low_eur=typical_low_eur,
            typical_high_eur=typical_high_eur,
            error=error,
            api_calls=api_calls,
        )
