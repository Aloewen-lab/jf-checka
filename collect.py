"""Collector: Suchgitter planen, messen, persistieren.

Aufrufbeispiele:
    python collect.py --dry-run          # Plan zeigen, keine API-Calls
    python collect.py                    # regulärer Lauf
    python collect.py --force-all        # Kadenz ignorieren
    python collect.py --max-calls 5      # Budget hart begrenzen

Ausführungsreihenfolge entspricht der Priorität aus config.yaml
(core > split_trigger > fringe > split_scheduled): läuft das Kontingent aus,
fallen zuerst die unwichtigen Messungen weg, nicht die täglichen Kern-Paare.
"""

from __future__ import annotations

import argparse
import itertools
import os
import pathlib
import sys
from datetime import date, datetime, timezone

import yaml
from dotenv import load_dotenv

import alerts
import store
from models import Offer, PricePoint, SearchRequest, Snapshot
from notify import SmtpConfigError
from providers import SerpApiProvider

ROOT = pathlib.Path(__file__).resolve().parent


# ------------------------------------------------------------------ Planung


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def all_pairs(cfg: dict) -> list[tuple[str, str]]:
    return list(itertools.product(cfg["dates"]["outbound"], cfg["dates"]["inbound"]))


def core_pairs(cfg: dict) -> list[tuple[str, str]]:
    return [tuple(p) for p in cfg["dates"]["core"]]


def make_request(cfg: dict, pair: tuple[str, str], adults: int) -> SearchRequest:
    return SearchRequest(
        origin=cfg["route"]["origin"],
        destination=cfg["route"]["destination"],
        outbound_date=pair[0],
        return_date=pair[1],
        adults=adults,
        travel_class=cfg["route"].get("travel_class", 1),
    )


def is_due(state: dict, key: str, every_n_days: int, today: date, force: bool) -> bool:
    if force:
        return True
    last = state.get("last_checked", {}).get(key)
    if not last:
        return True
    return (today - date.fromisoformat(last)).days >= every_n_days


def build_plan(cfg: dict, state: dict, today: date, force: bool) -> dict[str, list[SearchRequest]]:
    """Liefert die fälligen Anfragen, gruppiert nach Prioritätsklasse.

    split_trigger fehlt hier bewusst — der entsteht erst aus den Ergebnissen
    der Kern-Messungen.
    """
    cad = cfg["cadence"]
    primary = cfg["passenger_configs"]["primary"]
    cores = core_pairs(cfg)
    fringe = [p for p in all_pairs(cfg) if p not in cores]

    plan: dict[str, list[SearchRequest]] = {
        "core": [], "fringe": [], "split_scheduled": [], "reference": []
    }

    for pair in cores:
        req = make_request(cfg, pair, primary)
        if is_due(state, req.key, cad["core_primary_every_n_days"], today, force):
            plan["core"].append(req)

    for pair in fringe:
        req = make_request(cfg, pair, primary)
        if is_due(state, req.key, cad["fringe_primary_every_n_days"], today, force):
            plan["fringe"].append(req)

    for pair in cores:
        for adults in cfg["passenger_configs"]["split"]:
            req = make_request(cfg, pair, adults)
            if is_due(state, req.key, cad["split_check_every_n_days"], today, force):
                plan["split_scheduled"].append(req)

    ref = cfg.get("reference_history") or {}
    if ref.get("enabled"):
        plan["reference"] = []
        for pair in ref.get("pairs", []):
            req = make_request(cfg, tuple(pair), ref.get("adults", 1))
            if is_due(state, req.key, ref.get("every_n_days", 7), today, force):
                plan["reference"].append(req)

    return plan


# ---------------------------------------------------------------- Ausführung


class Budget:
    """Harter Zähler. Bricht ab statt weiterzulaufen — ein überschrittenes
    Kontingent kostet auf dem Free-Tier keine Daten, sondern Fehler."""

    def __init__(self, allowed: int):
        self.allowed = max(allowed, 0)
        self.used = 0

    @property
    def left(self) -> int:
        return max(self.allowed - self.used, 0)

    def spend(self, n: int) -> None:
        self.used += n


def resolve_budget(provider: SerpApiProvider, cfg: dict, max_calls: int | None) -> tuple[int, str]:
    reserve = cfg["quota"].get("reserve_calls", 0)
    left = provider.searches_left()
    if left is None:
        # Kontostand nicht abrufbar: konservativ auf das Tagesmittel begrenzen.
        allowed = max(cfg["quota"]["monthly_limit"] // 30, 1)
        note = f"Kontostand nicht abrufbar, konservatives Tagesbudget {allowed}"
    else:
        allowed = left - reserve
        note = f"{left} Suchen im Kontingent, {reserve} Reserve -> {max(allowed, 0)} nutzbar"
    if max_calls is not None:
        allowed = min(allowed, max_calls)
        note += f", per --max-calls auf {max_calls} begrenzt"
    return max(allowed, 0), note


def run_batch(
    provider: SerpApiProvider,
    requests_: list[SearchRequest],
    budget: Budget,
    label: str,
    offers: list[Offer],
    snapshots: list[Snapshot],
    history: list[PricePoint],
    state: dict,
    today: date,
) -> None:
    for req in requests_:
        if budget.left <= 0:
            print(f"  [{label}] Budget erschöpft, {len(requests_)} Anfragen übersprungen")
            return
        result = provider.search(req)
        budget.spend(result.snapshot.api_calls)
        snapshots.append(result.snapshot)
        offers.extend(result.offers)
        history.extend(result.price_history)

        if result.snapshot.status != "error":
            state.setdefault("last_checked", {})[req.key] = today.isoformat()

        price = result.snapshot.price_min_eur
        detail = (
            f"{price:>8.0f} EUR  ({result.snapshot.n_offers} Angebote, "
            f"{result.snapshot.price_level})"
            if price is not None
            else f"  {result.snapshot.status}: {(result.snapshot.error or '')[:60]}"
        )
        print(
            f"  [{label}] {req.outbound_date} -> {req.return_date}  {req.adults} Pax  {detail}"
        )


def detect_split_triggers(
    cfg: dict, snapshots_before: "object", fresh: list[Snapshot]
) -> list[SearchRequest]:
    """Split-Check auslösen, wenn ein Kern-Preis deutlich gestiegen ist.

    Ein Sprung nach oben heißt bei Gruppen fast immer: der billige Fare-Bucket
    hat nicht mehr genug Sitze. Genau dann kann getrenntes Buchen (3 + 2)
    wieder günstiger sein — vorher nicht.
    """
    threshold = cfg["cadence"].get("split_check_on_jump_pct")
    if not threshold:
        return []

    triggers: list[SearchRequest] = []
    for snap in fresh:
        if snap.status != "ok" or snap.price_min_eur is None:
            continue
        previous = store.last_known_min_price(
            snapshots_before, snap.outbound_date, snap.return_date, snap.adults
        )
        if previous is None:
            continue
        jump_pct = (snap.price_min_eur - previous) / previous * 100
        if jump_pct <= threshold:
            continue
        print(
            f"  ! Sprung +{jump_pct:.1f}% bei {snap.outbound_date} -> {snap.return_date} "
            f"({previous:.0f} -> {snap.price_min_eur:.0f} EUR), Split-Check ausgelöst"
        )
        for adults in cfg["passenger_configs"]["split"]:
            triggers.append(make_request(cfg, (snap.outbound_date, snap.return_date), adults))
    return triggers


# ------------------------------------------------------------------ Auswertung


def report_group_prices(cfg: dict, top_n: int = 5) -> None:
    """Gruppenpreis = min(p5, p3 + p2) je Datumspaar, aus der jüngsten Messung."""
    snaps = store.read_snapshots()
    if snaps.empty:
        return
    ok = snaps[(snaps["status"] == "ok") & snaps["price_min_eur"].notna()]
    if ok.empty:
        return

    latest = (
        ok.sort_values("ts_utc")
        .groupby(["outbound_date", "return_date", "adults"], as_index=False)
        .last()
    )
    primary = cfg["passenger_configs"]["primary"]
    a, b = cfg["passenger_configs"]["split"]

    rows = []
    for (out_d, ret_d), grp in latest.groupby(["outbound_date", "return_date"]):
        by_adults = dict(zip(grp["adults"], grp["price_min_eur"]))
        p_all = by_adults.get(primary)
        p_split = (
            by_adults[a] + by_adults[b] if a in by_adults and b in by_adults else None
        )
        candidates = [p for p in (p_all, p_split) if p is not None]
        if not candidates:
            continue
        best = min(candidates)
        rows.append(
            {
                "pair": f"{out_d} -> {ret_d}",
                "nights": (date.fromisoformat(ret_d) - date.fromisoformat(out_d)).days,
                "gruppe": best,
                "zusammen": p_all,
                "split_3_2": p_split,
            }
        )

    rows.sort(key=lambda r: r["gruppe"])
    print(f"\n== Günstigste Datumspaare (Gruppe, {cfg['group_size']} Personen) ==")
    for r in rows[:top_n]:
        split = f"{r['split_3_2']:.0f}" if r["split_3_2"] is not None else "  -"
        together = f"{r['zusammen']:.0f}" if r["zusammen"] is not None else "  -"
        saving = ""
        if r["split_3_2"] is not None and r["zusammen"] is not None:
            delta = r["zusammen"] - r["split_3_2"]
            saving = f"   Split spart {delta:.0f} EUR" if delta > 0 else "   Split bringt nichts"
        print(
            f"  {r['pair']}  {r['nights']:>2}N   Gruppe {r['gruppe']:>7.0f} EUR"
            f"   (zusammen {together}, 3+2 {split}){saving}"
        )


# ------------------------------------------------------------------------ CLI


def main() -> int:
    ap = argparse.ArgumentParser(description="JP-Flightwatch Collector")
    ap.add_argument("--dry-run", action="store_true", help="Plan zeigen, nichts abrufen")
    ap.add_argument("--force-all", action="store_true", help="Kadenz ignorieren")
    ap.add_argument("--max-calls", type=int, default=None, help="Budget begrenzen")
    ap.add_argument("--no-alerts", action="store_true", help="keine Mails versenden")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    cfg = load_config()
    state = store.load_state()
    today = datetime.now(timezone.utc).date()

    plan = build_plan(cfg, state, today, args.force_all)
    planned = sum(len(v) for v in plan.values())
    print(f"== Plan für {today} ==")
    for label in ("core", "fringe", "split_scheduled", "reference"):
        print(f"  {label:<16} {len(plan[label]):>3} Anfragen")
    print(f"  {'gesamt':<16} {planned:>3}")

    if args.dry_run:
        print("\n--dry-run: keine API-Calls.")
        return 0

    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("FEHLER: SERPAPI_KEY fehlt in .env", file=sys.stderr)
        return 2

    provider = SerpApiProvider(api_key)
    allowed, note = resolve_budget(provider, cfg, args.max_calls)
    budget = Budget(allowed)
    print(f"\n== Budget ==\n  {note}")
    if budget.allowed == 0:
        print("  Kein Budget verfügbar, Lauf beendet.")
        return 0

    snapshots_before = store.read_snapshots()
    offers: list[Offer] = []
    snapshots: list[Snapshot] = []
    history: list[PricePoint] = []

    def batch(reqs: list[SearchRequest], label: str) -> None:
        run_batch(
            provider, reqs, budget, label, offers, snapshots, history, state, today
        )

    print("\n== Messung ==")
    batch(plan["core"], "core")

    triggers = detect_split_triggers(cfg, snapshots_before, list(snapshots))
    if triggers:
        batch(triggers, "trigger")

    batch(plan["fringe"], "fringe")
    batch(plan["split_scheduled"], "split")
    # Zuletzt, weil es reiner Kontext ist: liefert Googles Preisgraph für einen
    # nahen Referenztermin auf derselben Route.
    batch(plan["reference"], "refhist")

    written = store.write_run(offers, snapshots, history)
    store.save_state(state)

    ok = sum(1 for s in snapshots if s.status == "ok")
    print(
        f"\n== Ergebnis ==\n  {budget.used} Calls verbraucht, "
        f"{ok}/{len(snapshots)} Messungen erfolgreich, {len(offers)} Angebote gespeichert"
    )
    if history:
        print(f"  {len(history)} Punkte aus Googles Preisgraph mitgespeichert")
    for kind, path in written.items():
        if path:
            print(f"  {kind}: {path.relative_to(ROOT)}")

    report_group_prices(cfg)

    if not args.no_alerts:
        print("\n== Alarme ==")
        try:
            n = alerts.dispatch(cfg)
            print(f"  {n} Mail(s) verschickt")
        except SmtpConfigError as exc:
            # Fehlende Mail-Konfiguration darf die Messung nicht verwerfen —
            # die Daten sind zu diesem Zeitpunkt bereits geschrieben.
            print(f"  Versand übersprungen: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
