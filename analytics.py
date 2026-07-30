"""Aggregationen über den Angebots-Store.

Bewusst UI-frei: das Dashboard (M2) und die Alarmregeln (M3) brauchen dieselben
Kennzahlen — Gruppenpreis, Tagesminimum, Rolling-Median, Allzeit-Tief. Doppelte
Implementierungen würden früher oder später auseinanderlaufen und Alarme
auslösen, die im Dashboard nicht sichtbar sind.

Alles rechnet auf `offers` (nicht auf `snapshots`), damit Filter wie
"max. 1 Stopp" sich konsistent auf KPIs, Verlauf und Heatmap auswirken.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

PRIMARY_ADULTS = 5
SPLIT_ADULTS = (3, 2)
FFILL_DAYS = 10  # max. Alter eines fortgeschriebenen Split-Preises


# ------------------------------------------------------------------- Filter


@dataclass
class Filters:
    max_stops: int | None = None
    max_duration_h: int | None = None
    arrival_airports: tuple[str, ...] | None = None
    airlines: tuple[str, ...] | None = None
    min_nights: int | None = None
    max_nights: int | None = None


def nights(outbound_date: str, return_date: str) -> int:
    return (date.fromisoformat(return_date) - date.fromisoformat(outbound_date)).days


def apply_filters(offers: pd.DataFrame, f: Filters) -> pd.DataFrame:
    if offers.empty:
        return offers
    df = offers
    if f.max_stops is not None:
        df = df[df["stops_out"] <= f.max_stops]
    if f.max_duration_h is not None:
        df = df[df["duration_out_min"] <= f.max_duration_h * 60]
    if f.arrival_airports:
        df = df[df["arrival_airport"].isin(f.arrival_airports)]
    if f.airlines:
        # airlines ist ein kommagetrennter String; ein Angebot zählt, wenn eine
        # der gewählten Airlines beteiligt ist.
        pattern = "|".join(pd.Series(list(f.airlines)).str.replace(r"([().*+?])", r"\\\1", regex=True))
        df = df[df["airlines"].str.contains(pattern, case=False, na=False)]
    if f.min_nights is not None or f.max_nights is not None:
        n = df.apply(lambda r: nights(r["outbound_date"], r["return_date"]), axis=1)
        if f.min_nights is not None:
            df = df[n >= f.min_nights]
        if f.max_nights is not None:
            df = df[n <= f.max_nights]
    return df


# --------------------------------------------------------------- Gruppenpreis


def per_config_daily_min(offers: pd.DataFrame) -> pd.DataFrame:
    """Günstigster Preis je (Tag, Datumspaar, Passagierzahl).

    Mehrere Läufe pro Tag werden auf ihr Minimum reduziert — das ist der Preis,
    der an diesem Tag buchbar gewesen wäre.
    """
    if offers.empty:
        return pd.DataFrame(
            columns=["day", "outbound_date", "return_date", "adults", "price_eur"]
        )
    df = offers.copy()
    df["day"] = df["ts_utc"].str.slice(0, 10)
    out = (
        df.groupby(["day", "outbound_date", "return_date", "adults"], as_index=False)[
            "price_eur"
        ]
        .min()
        .astype({"adults": "int16"})
    )
    return out


def group_prices(
    per_cfg: pd.DataFrame,
    primary: int = PRIMARY_ADULTS,
    split: tuple[int, int] = SPLIT_ADULTS,
    ffill_days: int = FFILL_DAYS,
) -> pd.DataFrame:
    """Gruppenpreis je (Tag, Datumspaar): min(p_primary, p_a + p_b).

    Die Split-Preise werden nur wöchentlich gemessen (Quota), deshalb werden sie
    über maximal `ffill_days` Tage fortgeschrieben und als geschätzt markiert.
    Ohne Fortschreibung hätte die Zeitreihe des Gruppenpreises an sechs von
    sieben Tagen keinen Split-Vergleich.
    """
    cols = [
        "day", "outbound_date", "return_date", "nights",
        "p_all", "p_a", "p_b", "p_split", "group_price",
        "split_saving", "source", "split_estimated",
    ]
    if per_cfg.empty:
        return pd.DataFrame(columns=cols)

    a, b = split
    wide = per_cfg.pivot_table(
        index=["outbound_date", "return_date", "day"],
        columns="adults",
        values="price_eur",
        aggfunc="min",
    ).reset_index()
    wide.columns.name = None
    for adults in (primary, a, b):
        if adults not in wide.columns:
            wide[adults] = pd.NA

    all_days = sorted(per_cfg["day"].unique())
    frames = []
    for (out_d, ret_d), grp in wide.groupby(["outbound_date", "return_date"]):
        g = (
            grp.set_index("day")
            .reindex(all_days)
            .rename(columns={primary: "p_all", a: "p_a", b: "p_b"})
        )
        raw_a, raw_b = g["p_a"].copy(), g["p_b"].copy()
        g[["p_a", "p_b"]] = g[["p_a", "p_b"]].ffill(limit=ffill_days)
        g["split_estimated"] = (raw_a.isna() & g["p_a"].notna()) | (
            raw_b.isna() & g["p_b"].notna()
        )
        g["outbound_date"], g["return_date"] = out_d, ret_d
        frames.append(g.reset_index(names="day"))

    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["p_all", "p_a", "p_b"], how="all")

    df["p_split"] = df["p_a"] + df["p_b"]
    df["group_price"] = df[["p_all", "p_split"]].min(axis=1)
    df = df.dropna(subset=["group_price"])
    df["split_saving"] = (df["p_all"] - df["p_split"]).where(
        df["p_all"].notna() & df["p_split"].notna()
    )
    df["source"] = "zusammen"
    df.loc[df["p_split"] <= df["p_all"].fillna(float("inf")), "source"] = "3+2 getrennt"
    df["nights"] = [
        nights(o, r) for o, r in zip(df["outbound_date"], df["return_date"])
    ]
    return df[cols].sort_values(["day", "group_price"]).reset_index(drop=True)


def daily_best(group_df: pd.DataFrame) -> pd.DataFrame:
    """Bestes Datumspaar je Tag — die Zeitreihe für den Preisverlauf."""
    if group_df.empty:
        return pd.DataFrame(columns=["day", "group_price", "pair", "nights", "source"])
    idx = group_df.groupby("day")["group_price"].idxmin()
    best = group_df.loc[idx].copy()
    best["pair"] = best["outbound_date"] + " → " + best["return_date"]
    return best[["day", "group_price", "pair", "nights", "source"]].sort_values("day")


def rolling_median(daily: pd.DataFrame, window: int = 7) -> pd.Series:
    if daily.empty:
        return pd.Series(dtype="float64")
    return daily["group_price"].rolling(window, min_periods=1).median()


# ------------------------------------------------------------------- Kennzahlen


@dataclass
class Kpis:
    current: float | None
    current_pair: str | None
    current_source: str | None
    delta_prev_day: float | None
    delta_median_7d: float | None
    all_time_low: float | None
    all_time_low_day: str | None
    is_new_low: bool
    days_to_departure: int | None
    n_days_tracked: int


def kpis(daily: pd.DataFrame, today: date | None = None) -> Kpis:
    if daily.empty:
        return Kpis(None, None, None, None, None, None, None, False, None, 0)

    daily = daily.sort_values("day").reset_index(drop=True)
    last = daily.iloc[-1]
    current = float(last["group_price"])

    prev = float(daily.iloc[-2]["group_price"]) if len(daily) > 1 else None
    med = float(rolling_median(daily).iloc[-1])

    low_idx = daily["group_price"].idxmin()
    low = float(daily.loc[low_idx, "group_price"])
    low_day = str(daily.loc[low_idx, "day"])

    out_date = str(last["pair"]).split(" → ")[0]
    ref = today or date.today()
    dtd = (date.fromisoformat(out_date) - ref).days

    return Kpis(
        current=current,
        current_pair=str(last["pair"]),
        current_source=str(last["source"]),
        delta_prev_day=None if prev is None else current - prev,
        # Am ersten Tag ist der Median der Wert selbst -> keine Aussage.
        delta_median_7d=None if len(daily) < 2 else current - med,
        all_time_low=low,
        all_time_low_day=low_day,
        is_new_low=bool(low_day == str(last["day"]) and len(daily) > 1),
        days_to_departure=dtd,
        n_days_tracked=int(daily["day"].nunique()),
    )


# ------------------------------------------------------------------ Datenlage


def data_health(snapshots: pd.DataFrame) -> dict:
    """Damit ein stiller Collector-Ausfall sichtbar wird und nicht als
    'Preis unverändert' durchgeht."""
    if snapshots.empty:
        return {"last_run": None, "n_ok": 0, "n_failed": 0, "errors": []}
    failed = snapshots[snapshots["status"] == "error"]
    return {
        "last_run": str(snapshots["ts_utc"].max()),
        "n_ok": int((snapshots["status"] == "ok").sum()),
        "n_failed": int(len(failed)),
        "errors": failed["error"].dropna().unique().tolist()[:3],
    }
