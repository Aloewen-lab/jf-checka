"""Persistenz: append-only Parquet, partitioniert nach Tag.

Jeder Lauf schreibt NEUE Dateien statt bestehende zu ändern. Damit bleibt das
Committen in Git sauber (keine Binär-Diffs) und die Historie ist unveränderlich.
Die Dtypes sind explizit, damit die Dateien über Monate hinweg dasselbe Schema
haben — sonst kippt eine durchgängig leere Spalte (z. B. carbon_kg) beim
Zusammenlesen die Typinferenz.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

import pandas as pd

from models import Offer, PricePoint, Snapshot

BASE = pathlib.Path(__file__).resolve().parent / "data"
OFFERS_DIR = BASE / "offers"
SNAPSHOTS_DIR = BASE / "snapshots"
HISTORY_DIR = BASE / "price_history"
STATE_FILE = BASE / "collector_state.json"

OFFER_DTYPES: dict[str, str] = {
    "ts_utc": "string",
    "provider": "string",
    "params_hash": "string",
    "origin": "string",
    "destination": "string",
    "outbound_date": "string",
    "return_date": "string",
    "adults": "int16",
    "bags": "int16",
    "price_eur": "float64",
    "price_per_person_eur": "float64",
    "bucket": "string",
    "arrival_airport": "string",
    "airlines": "string",
    "stops_out": "int16",
    "duration_out_min": "int32",
    "layovers": "string",
    "departure_time": "string",
    "arrival_time": "string",
    "travel_class": "string",
    "carbon_kg": "float64",
    "deep_link": "string",
}

SNAPSHOT_DTYPES: dict[str, str] = {
    "ts_utc": "string",
    "provider": "string",
    "params_hash": "string",
    "origin": "string",
    "destination": "string",
    "outbound_date": "string",
    "return_date": "string",
    "adults": "int16",
    "bags": "int16",
    "status": "string",
    "n_offers": "int32",
    "price_min_eur": "float64",
    "price_level": "string",
    "typical_low_eur": "float64",
    "typical_high_eur": "float64",
    "error": "string",
    "api_calls": "int16",
}

HISTORY_DTYPES: dict[str, str] = {
    "ts_utc": "string",
    "provider": "string",
    "outbound_date": "string",
    "return_date": "string",
    "adults": "int16",
    "hist_date": "string",
    "price_eur": "float64",
}


def _coerce(rows: list[dict], dtypes: dict[str, str]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(dtypes))
    return df.astype(dtypes)


def _read_dir(directory: pathlib.Path, dtypes: dict[str, str]) -> pd.DataFrame:
    files = sorted(directory.glob("dt=*/*.parquet"))
    if not files:
        return _coerce([], dtypes)
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    missing = [c for c in dtypes if c not in df.columns]
    # Vorwärtskompatibilität: Altbestand vor einer Schema-Erweiterung bekommt
    # Default-Werte (bags=0 heißt: gemessen ohne Gepäck-Parameter).
    defaults = {"bags": 0}
    for col in missing:
        df[col] = defaults.get(col, pd.NA)
    return df[list(dtypes)].astype(dtypes)


# ------------------------------------------------------------------- schreiben


def write_run(
    offers: list[Offer],
    snapshots: list[Snapshot],
    history: list[PricePoint] | None = None,
    run_id: str | None = None,
) -> dict[str, pathlib.Path | None]:
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    run_id = run_id or now.strftime("%H%M%S")

    written: dict[str, pathlib.Path | None] = {
        "offers": None, "snapshots": None, "price_history": None
    }

    for kind, rows, directory, dtypes in (
        ("offers", offers, OFFERS_DIR, OFFER_DTYPES),
        ("snapshots", snapshots, SNAPSHOTS_DIR, SNAPSHOT_DTYPES),
        ("price_history", history or [], HISTORY_DIR, HISTORY_DTYPES),
    ):
        if not rows:
            continue
        target = directory / f"dt={day}"
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{kind}-{run_id}.parquet"
        _coerce([r.as_row() for r in rows], dtypes).to_parquet(
            path, index=False, compression="snappy"
        )
        written[kind] = path

    return written


# ---------------------------------------------------------------------- lesen


def read_offers() -> pd.DataFrame:
    return _read_dir(OFFERS_DIR, OFFER_DTYPES)


def read_snapshots() -> pd.DataFrame:
    return _read_dir(SNAPSHOTS_DIR, SNAPSHOT_DTYPES)


def read_price_history() -> pd.DataFrame:
    """Googles eigener Preisgraph, soweit geliefert. Für Ostern 2027 aktuell leer —
    Google baut die Reihe erst, wenn der Termin näher rückt."""
    return _read_dir(HISTORY_DIR, HISTORY_DTYPES)


def last_known_min_price(
    snapshots: pd.DataFrame, outbound_date: str, return_date: str, adults: int
) -> float | None:
    """Günstigster Preis der jüngsten erfolgreichen Messung dieser Kombination.

    Basis für die Sprung-Erkennung, die den Split-Check auslöst.
    """
    if snapshots.empty:
        return None
    mask = (
        (snapshots["outbound_date"] == outbound_date)
        & (snapshots["return_date"] == return_date)
        & (snapshots["adults"] == adults)
        & (snapshots["status"] == "ok")
        & snapshots["price_min_eur"].notna()
    )
    hits = snapshots[mask]
    if hits.empty:
        return None
    return float(hits.sort_values("ts_utc").iloc[-1]["price_min_eur"])


# ---------------------------------------------------------------------- state


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"last_checked": {}}
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
