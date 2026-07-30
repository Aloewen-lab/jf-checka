"""Streamlit-Dashboard: Preisniveau und aktuelle Angebote BER -> Tokio.

Farbrollen stammen aus der validierten Referenzpalette (Oberfläche #fcfcfb,
kategoriale Slots 1+2 geprüft: normal ΔE 33.6, protan 24.7). Deshalb ist das
Theme in .streamlit/config.toml fest auf hell gesetzt — ein automatischer
Dark-Flip wäre nicht validiert.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

import analytics
import store
from analytics import Filters

# --------------------------------------------------------------- Farbrollen

SURFACE = "#fcfcfb"
GRID = "#e6e5e1"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
TEXT_MUTED = "#83827d"
SERIES_1 = "#2a78d6"  # Gruppenpreis
SERIES_2 = "#eb6834"  # Vergleich "alle zusammen"
STATUS_GOOD = "#0ca30c"
STATUS_CRITICAL = "#d03b3b"
SEQ_BLUE = [
    [0.00, "#cde2fb"], [0.25, "#86b6ef"], [0.50, "#3987e5"],
    [0.75, "#256abf"], [1.00, "#0d366b"],
]

ROOT = store.BASE.parent

st.set_page_config(
    page_title="JP-Flightwatch — BER → Tokio, Ostern 2027",
    page_icon="🛫",
    layout="wide",
)


# ------------------------------------------------------------------- Laden


@st.cache_data(ttl=300)
def load() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    return store.read_offers(), store.read_snapshots(), cfg


offers_all, snapshots, cfg = load()
group_size = cfg["group_size"]

if offers_all.empty:
    st.title("JP-Flightwatch")
    st.warning("Noch keine Daten. Erst `python collect.py` ausführen.")
    st.stop()


# ------------------------------------------------------------------ Sidebar

st.sidebar.header("Ansicht")
per_person = st.sidebar.toggle(
    "Preise pro Person", value=False, help=f"Aus: Gesamtpreis für {group_size} Personen"
)
unit_divisor = group_size if per_person else 1
unit_label = "€ p. P." if per_person else "€ gesamt"

st.sidebar.header("Filter")
max_stops = st.sidebar.select_slider(
    "Max. Stopps (Hinflug)", options=[0, 1, 2, 3], value=2
)
dur_max = int(offers_all["duration_out_min"].max() // 60) + 1
max_duration_h = st.sidebar.slider("Max. Reisedauer (h)", 8, dur_max, dur_max)
airports = sorted(offers_all["arrival_airport"].dropna().unique().tolist())
arrival = st.sidebar.multiselect("Ankunft", airports, default=airports)

all_nights = sorted(
    {
        analytics.nights(o, r)
        for o, r in zip(offers_all["outbound_date"], offers_all["return_date"])
    }
)
n_lo, n_hi = st.sidebar.select_slider(
    "Nächte", options=all_nights, value=(all_nights[0], all_nights[-1])
)

airline_pool = sorted(
    {a.strip() for s in offers_all["airlines"].dropna() for a in s.split(",") if a.strip()}
)
airlines = st.sidebar.multiselect("Airlines (leer = alle)", airline_pool, default=[])

filters = Filters(
    max_stops=max_stops,
    max_duration_h=max_duration_h,
    arrival_airports=tuple(arrival) if arrival else None,
    airlines=tuple(airlines) if airlines else None,
    min_nights=n_lo,
    max_nights=n_hi,
)

offers = analytics.apply_filters(offers_all, filters)
if offers.empty:
    st.title("JP-Flightwatch")
    st.warning("Kein Angebot passt zu diesen Filtern.")
    st.stop()

per_cfg = analytics.per_config_daily_min(offers)
group_df = analytics.group_prices(
    per_cfg,
    primary=cfg["passenger_configs"]["primary"],
    split=tuple(cfg["passenger_configs"]["split"]),
)
daily = analytics.daily_best(group_df)
k = analytics.kpis(daily, today=date.today())


# --------------------------------------------------------------------- Kopf

st.markdown(
    f"""
    <style>
      .kpi-row {{ display:flex; gap:12px; flex-wrap:wrap; margin:4px 0 20px; }}
      .kpi {{ flex:1 1 150px; background:{SURFACE}; border:1px solid {GRID};
              border-radius:10px; padding:12px 14px; }}
      .kpi .label {{ font-size:12px; color:{TEXT_SECONDARY}; letter-spacing:.02em;
                     min-height:2.4em; }}
      .kpi .value {{ font-size:24px; font-weight:600; color:{TEXT_PRIMARY};
                     line-height:1.2; white-space:nowrap;
                     font-variant-numeric:tabular-nums; }}
      .kpi .sub {{ font-size:12px; color:{TEXT_MUTED}; }}
      .kpi .delta {{ font-size:13px; font-weight:600; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("BER → Tokio · Osterferien 2027")
st.caption(
    f"{cfg['route']['destination_label']} · {group_size} Reisende · Economy · "
    f"Hinflug {min(cfg['dates']['outbound'])}–{max(cfg['dates']['outbound'])}, "
    f"Rückflug {min(cfg['dates']['inbound'])}–{max(cfg['dates']['inbound'])}"
)


def fmt(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "–"
    return f"{v / unit_divisor:,.0f}".replace(",", ".")


def delta_html(value: float | None, *, invert_good: bool = True) -> str:
    """Icon + Wort + Farbe. Nie Farbe allein — die Statusfarben der Palette
    liegen im hellen Modus teils unter 3:1."""
    if value is None or pd.isna(value) or abs(value) < 1:
        return f'<div class="sub">unverändert</div>'
    cheaper = value < 0
    good = cheaper if invert_good else not cheaper
    color = STATUS_GOOD if good else STATUS_CRITICAL
    icon = "▼" if cheaper else "▲"
    word = "günstiger" if cheaper else "teurer"
    return (
        f'<div class="delta" style="color:{color}">{icon} {fmt(abs(value))} {word}</div>'
    )


def tile(label: str, value: str, sub: str) -> str:
    if not sub.startswith("<"):
        sub = f'<div class="sub">{sub}</div>'
    return (
        f'<div class="kpi"><div class="label">{label}</div>'
        f'<div class="value">{value}</div>{sub}</div>'
    )


unit_suffix = "p. P." if per_person else "gesamt"
no_history = "erst ab dem 2. Messtag"

cards = [
    tile(
        f"Günstigster Gruppenpreis ({unit_suffix})",
        f"{fmt(k.current)} €",
        f"{k.current_pair or '–'} · {k.current_source or ''}",
    ),
    tile(
        "Gegenüber Vortag",
        f"{fmt(k.delta_prev_day)} €" if k.delta_prev_day is not None else "–",
        delta_html(k.delta_prev_day) if k.delta_prev_day is not None else no_history,
    ),
    tile(
        "Gegenüber 7-Tage-Median",
        f"{fmt(k.delta_median_7d)} €" if k.delta_median_7d is not None else "–",
        delta_html(k.delta_median_7d) if k.delta_median_7d is not None else no_history,
    ),
    tile(
        f"Allzeit-Tief ({unit_suffix})",
        f"{fmt(k.all_time_low)} €",
        f"am {k.all_time_low_day}" + (" · neu!" if k.is_new_low else ""),
    ),
    tile(
        "Tage bis Abflug",
        f"{k.days_to_departure}",
        f"{k.n_days_tracked} Tag(e) Historie",
    ),
]
st.markdown(f'<div class="kpi-row">{"".join(cards)}</div>', unsafe_allow_html=True)

# Split-Hinweis
latest_day = group_df["day"].max()
today_rows = group_df[group_df["day"] == latest_day]
best_row = today_rows.loc[today_rows["group_price"].idxmin()]
saving = best_row["split_saving"]
if pd.notna(saving) and saving > 0:
    est = " (Split-Preis fortgeschrieben)" if best_row["split_estimated"] else ""
    st.success(
        f"**Getrennt buchen spart {saving / unit_divisor:,.0f} {unit_label}** "
        f"auf {best_row['outbound_date']} → {best_row['return_date']}: "
        f"3 Personen {best_row['p_a'] / unit_divisor:,.0f} + 2 Personen "
        f"{best_row['p_b'] / unit_divisor:,.0f} statt {best_row['p_all'] / unit_divisor:,.0f} "
        f"für alle zusammen.{est}".replace(",", ".")
    )


# --------------------------------------------------------------------- Tabs

tab_trend, tab_grid, tab_table = st.tabs(
    ["Preisverlauf", "Fare-Grid", "Aktuelle Angebote"]
)


def base_layout(fig: go.Figure, ytitle: str) -> go.Figure:
    fig.update_layout(
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        separators=",.",  # deutsches Zahlenformat: 7.230,50
        font=dict(color=TEXT_SECONDARY, size=13),
        margin=dict(l=8, r=8, t=48, b=8),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=SURFACE, bordercolor=GRID, font_color=TEXT_PRIMARY),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis=dict(showgrid=False, linecolor=GRID, ticks="outside", tickcolor=GRID),
        yaxis=dict(
            title=ytitle, gridcolor=GRID, zeroline=False, linecolor=GRID,
            tickformat=",.0f",
        ),
    )
    return fig


with tab_trend:
    if len(daily) < 2:
        st.info(
            f"Erst **{len(daily)} Messtag** in der Historie — der Verlauf entsteht ab "
            "dem zweiten Collector-Lauf. Unten der bisher gemessene Punkt."
        )

    d = daily.copy()
    d["value"] = d["group_price"] / unit_divisor
    d["median"] = analytics.rolling_median(daily).values / unit_divisor

    fig = go.Figure()
    if len(d) >= 3:
        fig.add_trace(
            go.Scatter(
                x=d["day"], y=d["median"], name="7-Tage-Median",
                mode="lines", line=dict(color=TEXT_MUTED, width=2, dash="dot"),
                hovertemplate="%{y:,.0f}<extra>7-Tage-Median</extra>",
            )
        )
    fig.add_trace(
        go.Scatter(
            x=d["day"], y=d["value"], name="Günstigster Gruppenpreis",
            mode="lines+markers",
            line=dict(color=SERIES_1, width=2),
            marker=dict(size=9, color=SERIES_1, line=dict(width=2, color=SURFACE)),
            customdata=d[["pair", "nights", "source"]],
            hovertemplate=(
                "<b>%{y:,.0f} " + unit_label + "</b><br>%{customdata[0]}"
                "<br>%{customdata[1]} Nächte · %{customdata[2]}<extra></extra>"
            ),
        )
    )
    # Allzeit-Tief direkt beschriften statt eine Zahl an jeden Punkt zu hängen.
    if k.all_time_low is not None:
        fig.add_annotation(
            x=k.all_time_low_day, y=k.all_time_low / unit_divisor,
            text=f"Tief {fmt(k.all_time_low)}", showarrow=True, arrowhead=0,
            arrowcolor=TEXT_MUTED, ax=0, ay=-30,
            font=dict(color=TEXT_PRIMARY, size=12), bgcolor=SURFACE,
            bordercolor=GRID, borderwidth=1, borderpad=4,
        )
    fig.update_layout(title=f"Preisniveau seit Tracking-Start ({unit_label})")
    fig = base_layout(fig, unit_label)
    if len(d) < 3:
        # Bei einem einzigen Punkt wählt Plotly eine entartete Achse (7231–7231).
        v = float(d["value"].iloc[0])
        fig.update_yaxes(range=[v * 0.94, v * 1.06])
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Datenreihe als Tabelle"):
        show = daily.copy()
        show["Preis"] = (show["group_price"] / unit_divisor).round(0)
        st.dataframe(
            show[["day", "Preis", "pair", "nights", "source"]].rename(
                columns={"day": "Tag", "pair": "Datumspaar", "nights": "Nächte",
                         "source": "Buchungsart"}
            ),
            hide_index=True, use_container_width=True,
        )


with tab_grid:
    st.markdown(
        f"**Gruppenpreis je Datumspaar** ({unit_label}) — Stand {latest_day}. "
        "Dunkler ist teurer."
    )
    grid = today_rows.pivot_table(
        index="outbound_date", columns="return_date", values="group_price", aggfunc="min"
    ).sort_index()
    grid = grid / unit_divisor

    z = grid.values.astype(float)
    lo, hi = pd.Series(z.ravel()).min(), pd.Series(z.ravel()).max()
    span = (hi - lo) or 1.0
    text = [[("" if pd.isna(v) else f"{v:,.0f}".replace(",", ".")) for v in row] for row in z]
    # Beschriftungsfarbe folgt der Zellhelligkeit, sonst verschwindet Text in dunklen Zellen.
    tcol = [
        [SURFACE if (not pd.isna(v) and (v - lo) / span > 0.55) else TEXT_PRIMARY for v in row]
        for row in z
    ]

    hm = go.Figure(
        go.Heatmap(
            z=z, x=list(grid.columns), y=list(grid.index),
            colorscale=SEQ_BLUE, xgap=2, ygap=2,
            colorbar=dict(title=unit_label, outlinewidth=0, thickness=12),
            hovertemplate="Hin %{y}<br>Zurück %{x}<br><b>%{z:,.0f}</b><extra></extra>",
        )
    )
    for i, row in enumerate(text):
        for j, val in enumerate(row):
            if val:
                hm.add_annotation(
                    x=list(grid.columns)[j], y=list(grid.index)[i], text=val,
                    showarrow=False, font=dict(color=tcol[i][j], size=13),
                )
    hm.update_layout(
        # type="category" ist zwingend: als Zeitachse interpretiert Plotly die
        # Datums-Strings und verschiebt die Zellen gegen ihre Labels.
        xaxis=dict(
            type="category", side="top", showgrid=False, linecolor=SURFACE,
            title=dict(text="Rückflug", standoff=8), ticks="",
        ),
        yaxis=dict(
            type="category", autorange="reversed", showgrid=False,
            linecolor=SURFACE, title="Hinflug", ticks="",
        ),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, separators=",.",
        font=dict(color=TEXT_SECONDARY, size=13),
        margin=dict(l=8, r=8, t=64, b=8),
        hoverlabel=dict(bgcolor=SURFACE, bordercolor=GRID, font_color=TEXT_PRIMARY),
    )
    st.plotly_chart(hm, use_container_width=True)
    st.caption(
        "Kurze Reisen sind hier systematisch günstiger — über den Nächte-Filter "
        "links vergleichbar machen."
    )


with tab_table:
    # "Aktuell" heißt: die jüngste Messung JE Datumspaar und Passagierzahl.
    # Über den ganzen Tag zu filtern würde mehrere Läufe vermischen und
    # überholte Preise als aktuell ausgeben.
    latest_per_key = offers.groupby(
        ["outbound_date", "return_date", "adults"], as_index=False
    )["ts_utc"].max()
    current = offers.merge(
        latest_per_key, on=["outbound_date", "return_date", "adults", "ts_utc"]
    ).copy()
    latest_ts = offers["ts_utc"].max()
    current["Nächte"] = [
        analytics.nights(o, r)
        for o, r in zip(current["outbound_date"], current["return_date"])
    ]
    current["Dauer"] = current["duration_out_min"].map(
        lambda m: f"{m // 60}h{m % 60:02d}"
    )
    view = current[
        ["outbound_date", "return_date", "Nächte", "adults", "price_eur",
         "price_per_person_eur", "airlines", "arrival_airport", "stops_out",
         "Dauer", "layovers", "carbon_kg", "deep_link"]
    ].rename(
        columns={
            "outbound_date": "Hinflug", "return_date": "Rückflug", "adults": "Pax",
            "price_eur": "Preis (Buchung)", "price_per_person_eur": "€ p. P.",
            "airlines": "Airlines", "arrival_airport": "Ankunft",
            "stops_out": "Stopps", "layovers": "Umstiege", "carbon_kg": "CO₂ (kg)",
            "deep_link": "Google Flights",
        }
    ).sort_values("€ p. P.")

    st.markdown(
        f"**{len(view)} Angebote** aus der jeweils jüngsten Messung, "
        f"letzte um {str(latest_ts)[:16]} UTC"
    )
    st.dataframe(
        view, hide_index=True, use_container_width=True, height=520,
        column_config={
            "Google Flights": st.column_config.LinkColumn("Buchen", display_text="öffnen"),
            "Preis (Buchung)": st.column_config.NumberColumn(format="%.0f €"),
            "€ p. P.": st.column_config.NumberColumn(format="%.0f €"),
        },
    )
    st.caption(
        "„Preis (Buchung)“ ist der Gesamtpreis der jeweiligen Buchung für die Anzahl "
        "in der Spalte Pax — eine 2er-Zeile ist also kein Preis für die ganze Gruppe. "
        "Gepäck und Sitzplatzreservierung sind nicht enthalten."
    )


# ------------------------------------------------------------------- Fußzeile

health = analytics.data_health(snapshots)
cols = st.columns([2, 1, 1])
cols[0].caption(f"Letzte Messung: {health['last_run']} UTC")
cols[1].caption(f"Erfolgreiche Messungen: {health['n_ok']}")
if health["n_failed"]:
    cols[2].error(f"{health['n_failed']} fehlgeschlagen: {health['errors'][:1]}")
else:
    cols[2].caption("Keine Fehler")
