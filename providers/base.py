"""Provider-Schnittstelle.

Absichtlich schmal: `search()` und `searches_left()`. Damit ist ein Wechsel der
Datenquelle eine neue Datei, kein Umbau — nach dem Aus der Amadeus Self-Service
API (17.07.2026) ist das keine theoretische Vorsichtsmaßnahme.
"""

from __future__ import annotations

from typing import Protocol

from models import SearchRequest, SearchResult


class Provider(Protocol):
    name: str

    def search(self, req: SearchRequest) -> SearchResult:
        """Sucht Angebote. Wirft NICHT bei API-Fehlern, sondern liefert einen
        Snapshot mit status != "ok", damit Lücken protokolliert werden."""
        ...

    def searches_left(self) -> int | None:
        """Verbleibendes Monatskontingent, oder None wenn unbekannt."""
        ...
