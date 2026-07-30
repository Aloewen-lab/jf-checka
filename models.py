"""Datenmodelle. Bewusst quellenunabhängig, damit Provider austauschbar bleiben."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SearchRequest:
    origin: str
    destination: str  # IATA-Code oder Google-Knowledge-Graph-ID (z. B. /m/07dfk)
    outbound_date: str  # YYYY-MM-DD
    return_date: str
    adults: int
    travel_class: int = 1  # 1 = Economy
    currency: str = "EUR"
    # Gepäckstücke pro Person. Seit 30.07.2026 (nachmittags) Standard 1: getrackt wird der
    # Preis, den 5 Personen mit Koffern tatsächlich zahlen, nicht der
    # Light-Tarif, den niemand buchen würde. bags steckt im key, damit der
    # Metrik-Wechsel eine neue Zeitreihe beginnt statt die alte zu verfälschen.
    bags: int = 0

    @property
    def date_pair(self) -> str:
        return f"{self.outbound_date}_{self.return_date}"

    @property
    def key(self) -> str:
        """Stabiler Schlüssel für Kadenz-State und Zeitreihen-Gruppierung."""
        return (
            f"{self.origin}>{self.destination}|{self.date_pair}"
            f"|a{self.adults}|b{self.bags}"
        )

    @property
    def params_hash(self) -> str:
        return hashlib.sha1(self.key.encode()).hexdigest()[:12]


@dataclass
class Offer:
    ts_utc: str
    provider: str
    params_hash: str
    origin: str
    destination: str
    outbound_date: str
    return_date: str
    adults: int
    bags: int
    price_eur: float
    price_per_person_eur: float
    bucket: str  # "best" | "other" — Googles eigene Vorauswahl
    arrival_airport: str  # HND oder NRT
    airlines: str  # kommagetrennt, für Parquet-Freundlichkeit
    stops_out: int
    duration_out_min: int
    layovers: str
    departure_time: str
    arrival_time: str
    travel_class: str
    carbon_kg: float | None
    deep_link: str

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Snapshot:
    """Ein Messversuch — auch ein fehlgeschlagener.

    Ohne diese Zeile wäre eine Lücke in der Zeitreihe nicht von einem
    unveränderten Preis zu unterscheiden.
    """

    ts_utc: str
    provider: str
    params_hash: str
    origin: str
    destination: str
    outbound_date: str
    return_date: str
    adults: int
    bags: int
    status: str  # "ok" | "no_results" | "error"
    n_offers: int
    price_min_eur: float | None
    price_level: str | None  # Googles Einordnung: low | typical | high
    typical_low_eur: float | None
    typical_high_eur: float | None
    error: str | None
    api_calls: int

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PricePoint:
    """Ein Punkt aus Googles eigenem Preisgraph (`price_insights.price_history`).

    Google liefert das Feld nur für Termine, die nah genug sind — für Ostern 2027
    (Stand Juli 2026) noch nicht. Wir speichern es trotzdem ab, sobald es
    auftaucht: es kostet keinen zusätzlichen Call und liefert rückwirkend rund
    zwei Monate Historie, die sich sonst nicht rekonstruieren lässt.
    """

    ts_utc: str  # Zeitpunkt unserer Messung
    provider: str
    outbound_date: str
    return_date: str
    adults: int
    hist_date: str  # Tag, für den Google diesen Preis ausweist
    price_eur: float

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchResult:
    snapshot: Snapshot
    offers: list[Offer] = field(default_factory=list)
    price_history: list[PricePoint] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.snapshot.status == "ok"
