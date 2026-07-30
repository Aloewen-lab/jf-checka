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
import audit as audit_mod
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

APP_NAME = "JF-Checka"
# Sichtbar im Footer. Bei Deploy-Problemen sofort erkennbar, welcher Stand läuft.
BUILD = "2026-07-30.4"

st.set_page_config(
    page_title=f"{APP_NAME} — BER → Tokio, Ostern 2027",
    page_icon="🛫",
    layout="wide",
)


# ------------------------------------------------------------------- Laden


@st.cache_data(ttl=300)
def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    return (
        store.read_offers(),
        store.read_snapshots(),
        store.read_price_history(),
        cfg,
    )


offers_all, snapshots, google_history, cfg = load()
group_size = cfg["group_size"]

# Der Referenztermin (siehe reference_history in config.yaml) liegt in 2026 und
# darf die Ostern-Auswertung nicht verfälschen — er dient nur dazu, Googles
# Preisgraph abzuholen. Deshalb hart auf das konfigurierte Fenster einschränken.
_win_out = set(cfg["dates"]["outbound"])
_win_in = set(cfg["dates"]["inbound"])


def _in_window(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df["outbound_date"].isin(_win_out) & df["return_date"].isin(_win_in)]


offers_all = _in_window(offers_all)
snapshots = _in_window(snapshots)

if offers_all.empty:
    st.title(APP_NAME)
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
AIRPORT_NAMES = {
    "HND": "HND — Tokio Haneda (stadtnah)",
    "NRT": "NRT — Tokio Narita (~60 km östlich)",
    "KIX": "KIX — Osaka Kansai",
    "NGO": "NGO — Nagoya Chubu",
    "FUK": "FUK — Fukuoka",
    "CTS": "CTS — Sapporo New Chitose",
}
airports = sorted(offers_all["arrival_airport"].dropna().unique().tolist())
arrival = st.sidebar.multiselect(
    "Ankunftsflughafen",
    airports,
    default=airports,
    format_func=lambda a: AIRPORT_NAMES.get(a, a),
    help="Haneda liegt deutlich näher an der Stadt als Narita — bei gleichem "
    "Preis ist HND meist die bessere Wahl.",
)

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

st.title(APP_NAME)
st.markdown("**BER → Tokio · Osterferien 2027**")
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
MIN_SPLIT_SAVING = float(cfg.get("split_min_saving_eur", 0))
saving = best_row["split_saving"]
if pd.notna(saving) and saving >= MIN_SPLIT_SAVING:
    est = " (Split-Preis fortgeschrieben)" if best_row["split_estimated"] else ""
    st.success(
        f"**Getrennt buchen spart {saving / unit_divisor:,.0f} {unit_label}** "
        f"auf {best_row['outbound_date']} → {best_row['return_date']}: "
        f"3 Personen {best_row['p_a'] / unit_divisor:,.0f} + 2 Personen "
        f"{best_row['p_b'] / unit_divisor:,.0f} statt {best_row['p_all'] / unit_divisor:,.0f} "
        f"für alle zusammen.{est}".replace(",", ".")
    )


# --------------------------------------------------------------------- Tabs

tab_trend, tab_grid, tab_split, tab_table = st.tabs(
    ["Preisverlauf", "Fare-Grid", "Zusammen vs. 3+2", "Aktuelle Angebote"]
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
    # Echte Zeitachse statt Kategorien: nur so werden ausgefallene Messtage als
    # Lücke sichtbar und nicht stillschweigend zusammengeschoben.
    d["x"] = pd.to_datetime(d["day"])

    fig = go.Figure()
    if len(d) >= 3:
        fig.add_trace(
            go.Scatter(
                x=d["x"], y=d["median"], name="7-Tage-Median",
                mode="lines", line=dict(color=TEXT_MUTED, width=2, dash="dot"),
                hovertemplate="%{y:,.0f}<extra>7-Tage-Median</extra>",
            )
        )
    fig.add_trace(
        go.Scatter(
            x=d["x"], y=d["value"], name="Günstigster Gruppenpreis",
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
            x=pd.Timestamp(k.all_time_low_day), y=k.all_time_low / unit_divisor,
            text=f"Tief {fmt(k.all_time_low)}", showarrow=True, arrowhead=0,
            arrowcolor=TEXT_MUTED, ax=0, ay=-30,
            font=dict(color=TEXT_PRIMARY, size=12), bgcolor=SURFACE,
            bordercolor=GRID, borderwidth=1, borderpad=4,
        )
    fig.update_layout(title=f"Preisniveau seit Tracking-Start ({unit_label})")
    fig = base_layout(fig, unit_label)
    fig.update_xaxes(type="date", tickformat="%d.%m.", ticklabelmode="period")
    if len(d) <= 14:
        fig.update_xaxes(dtick=86_400_000)  # täglich, sonst erfindet Plotly Uhrzeiten
    if len(d) < 3:
        # Bei einem einzigen Punkt wählt Plotly sonst eine entartete Achse
        # (7231–7231 auf der y-, Mikrosekunden auf der x-Achse).
        v = float(d["value"].iloc[0])
        fig.update_yaxes(range=[v * 0.94, v * 1.06])
        mid = d["x"].iloc[0]
        fig.update_xaxes(
            range=[mid - pd.Timedelta(days=3), mid + pd.Timedelta(days=3)]
        )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Für die Ostern-2027-Termine selbst gibt es keine Rückschau: Googles Preisgraph "
        "entsteht erst, wenn ein Termin näher rückt. Der Collector speichert ihn "
        "automatisch, sobald er für diese Daten auftaucht."
    )

    # --- Routen-Preisniveau als Kontext ------------------------------------
    if not google_history.empty:
        ref = google_history.copy()
        newest = ref["ts_utc"].max()
        ref = ref[ref["ts_utc"] == newest]
        ref["pro_person"] = ref["price_eur"] / ref["adults"]
        ref["x"] = pd.to_datetime(ref["hist_date"])
        ref = ref.sort_values("x")
        ref_pair = f"{ref['outbound_date'].iloc[0]} → {ref['return_date'].iloc[0]}"

        st.markdown("---")
        st.markdown("**Routen-Preisniveau BER → Tokio (Googles Rückschau)**")

        hist_fig = go.Figure(
            go.Scatter(
                x=ref["x"], y=ref["pro_person"], mode="lines",
                line=dict(color=SERIES_1, width=2),
                name="Günstigster Preis pro Person",
                hovertemplate="%{x|%d.%m.%Y}<br><b>%{y:,.0f} €</b> p. P.<extra></extra>",
            )
        )
        hist_fig.update_layout(
            title=f"Referenztermin {ref_pair} · 1 Person · {len(ref)} Tage",
        )
        st.plotly_chart(base_layout(hist_fig, "€ pro Person"), use_container_width=True)

        first, last = float(ref["pro_person"].iloc[0]), float(ref["pro_person"].iloc[-1])
        trend = "gestiegen" if last > first else "gefallen"
        st.caption(
            f"**Das sind nicht die Ostern-Preise.** Diese Reihe zeigt einen "
            f"Referenztermin im Herbst 2026, für den Google eine Historie führt — "
            f"gedacht als Kontext für das allgemeine Preisniveau der Strecke. "
            f"Im gezeigten Zeitraum ist es von {first:,.0f} € auf {last:,.0f} € "
            f"pro Person {trend}. Saisonal ist Ostern nicht mit dem Herbst "
            f"vergleichbar, die Reihe taugt also für den Trend der Strecke, "
            f"nicht als Prognose für unsere Termine.".replace(",", ".")
        )

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
        "Jede Zelle zeigt den **besseren der beiden Buchungswege** — alle zusammen "
        "oder 3+2 getrennt. Welcher das ist, steht im Tab „Zusammen vs. 3+2“. "
        "Kurze Reisen sind hier systematisch günstiger; über den Nächte-Filter links "
        "vergleichbar machen."
    )


with tab_split:
    st.markdown(
        f"**Alle {group_size} zusammen buchen oder in 3 + 2 aufteilen?** "
        f"Stand {latest_day}, {unit_label}."
    )

    cmp_df = today_rows.dropna(subset=["p_all", "p_split"]).copy()
    if cmp_df.empty:
        st.info(
            "Für kein Datumspaar liegen beide Messungen vor. Der Split-Check läuft "
            f"alle {cfg['cadence']['split_check_every_n_days']} Tage auf den Kern-Paaren "
            f"und zusätzlich sofort, wenn ein Preis um mehr als "
            f"{cfg['cadence']['split_check_on_jump_pct']} % springt."
        )
    else:
        cmp_df["pair"] = cmp_df["outbound_date"] + " → " + cmp_df["return_date"]
        cmp_df = cmp_df.sort_values("group_price")

        # Dot-Plot statt Balken: der Abstand zwischen den Punkten IST die Aussage.
        # Balken bräuchten eine Nullachse, auf der 390 EUR von 7.620 EUR
        # verschwinden; ein Punktdiagramm darf legitim zoomen.
        p_all_v = cmp_df["p_all"] / unit_divisor
        p_split_v = cmp_df["p_split"] / unit_divisor

        conn_x: list[float | None] = []
        conn_y: list[str | None] = []
        for pair, a_val, b_val in zip(cmp_df["pair"], p_all_v, p_split_v):
            conn_x += [a_val, b_val, None]
            conn_y += [pair, pair, None]

        dots = go.Figure()
        dots.add_trace(
            go.Scatter(
                x=conn_x, y=conn_y, mode="lines",
                line=dict(color=GRID, width=3),
                showlegend=False, hoverinfo="skip",
            )
        )
        dots.add_trace(
            go.Scatter(
                x=p_all_v, y=cmp_df["pair"], mode="markers",
                name=f"Alle {group_size} zusammen",
                marker=dict(size=13, color=SERIES_1, line=dict(width=2, color=SURFACE)),
                hovertemplate="%{x:,.0f}<extra>zusammen</extra>",
            )
        )
        dots.add_trace(
            go.Scatter(
                x=p_split_v, y=cmp_df["pair"], mode="markers",
                name="3 + 2 getrennt",
                marker=dict(size=13, color=SERIES_2, line=dict(width=2, color=SURFACE)),
                hovertemplate="%{x:,.0f}<extra>3 + 2</extra>",
            )
        )
        for pair, saving, b_val in zip(
            cmp_df["pair"], cmp_df["split_saving"] / unit_divisor, p_split_v
        ):
            if saving and saving > 0:
                dots.add_annotation(
                    x=b_val, y=pair, text=f"−{saving:,.0f} €".replace(",", "."),
                    showarrow=False, xanchor="right", xshift=-14,
                    font=dict(color=TEXT_PRIMARY, size=12),
                )

        lo_x = float(min(p_all_v.min(), p_split_v.min()))
        hi_x = float(max(p_all_v.max(), p_split_v.max()))
        pad = max((hi_x - lo_x) * 0.35, hi_x * 0.01)
        dots.update_layout(
            paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, separators=",.",
            font=dict(color=TEXT_SECONDARY, size=13),
            margin=dict(l=8, r=8, t=52, b=8), height=90 + 62 * len(cmp_df),
            legend=dict(orientation="h", yanchor="bottom", y=1.04, x=0),
            hoverlabel=dict(bgcolor=SURFACE, bordercolor=GRID, font_color=TEXT_PRIMARY),
            xaxis=dict(
                title=f"{unit_label} — weiter links ist günstiger",
                gridcolor=GRID, zeroline=False, linecolor=GRID,
                tickformat=",.0f", range=[lo_x - pad, hi_x + pad * 0.4],
            ),
            yaxis=dict(
                type="category", showgrid=False, linecolor=SURFACE,
                autorange="reversed", ticks="",
            ),
        )
        st.plotly_chart(dots, use_container_width=True)
        st.caption(
            "Die Achse beginnt nicht bei 0 — sie ist auf den Preisbereich gezoomt, "
            "damit kleine Unterschiede sichtbar werden. Liegen zwei Punkte "
            "übereinander, bringt die Aufteilung an diesem Termin nichts."
        )

        table = cmp_df.copy()
        table["Ersparnis"] = table["split_saving"] / unit_divisor
        table["Empfehlung"] = [
            "3 + 2 getrennt" if s > 0 else "zusammen" for s in table["split_saving"]
        ]
        table["Split geschätzt"] = table["split_estimated"].map({True: "ja", False: "nein"})
        for col, src in (
            ("Alle zusammen", "p_all"), ("3er-Buchung", "p_a"),
            ("2er-Buchung", "p_b"), ("Summe 3+2", "p_split"),
        ):
            table[col] = table[src] / unit_divisor

        st.dataframe(
            table[
                ["pair", "nights", "Alle zusammen", "3er-Buchung", "2er-Buchung",
                 "Summe 3+2", "Ersparnis", "Empfehlung", "Split geschätzt"]
            ].rename(columns={"pair": "Datumspaar", "nights": "Nächte"}),
            hide_index=True, use_container_width=True,
            column_config={
                c: st.column_config.NumberColumn(format="%.0f €")
                for c in ("Alle zusammen", "3er-Buchung", "2er-Buchung",
                          "Summe 3+2", "Ersparnis")
            },
        )
        st.caption(
            "**Warum das überhaupt etwas bringt:** Airlines verkaufen in Fare-Buckets mit "
            "begrenzter Sitzzahl. Eine Suche für 5 Personen zeigt nur Angebote, bei denen "
            "alle 5 Plätze im selben Bucket frei sind — sind es nur noch 3, fällt der "
            "billige Preis aus dem Ergebnis. Zwei getrennte Buchungen können ihn wieder "
            "erreichen. Preis dafür: kein gemeinsames Ticket, getrennte Umbuchung, "
            "Sitzplätze ggf. auseinander."
        )
        missing = len(today_rows) - len(cmp_df)
        if missing:
            st.caption(
                f"Für {missing} weitere Datumspaare fehlt noch die Split-Messung — "
                "dort steht im Fare-Grid der Preis für alle zusammen."
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
        "Seit 30.07.2026 wird mit 1 aufgegebenem Gepäckstück pro Person gesucht "
        "(`bags=1`); Light-Tarife ohne Koffer werden herausgefiltert oder umbepreist, "
        "soweit Google die Gebühren kennt. Sitzplatzreservierung nicht enthalten."
    )

    aud = audit_mod.latest()
    if aud:
        with st.expander(f"Gepäck-Audit vom {aud['ts_utc'][:10]}"):
            it = aud["itinerary"]
            st.markdown(
                f"Geprüft wurde das günstigste Angebot "
                f"**{aud['pair']['outbound']} → {aud['pair']['return']}** "
                f"({it['airlines']}, {it['price_booking']:,.0f} € für "
                f"{aud['adults']} Personen):".replace(",", ".")
            )
            for opt in aud.get("booking_options") or []:
                bag = "; ".join(opt.get("baggage") or []) or "keine Gepäckangabe"
                st.markdown(f"- **{opt.get('book_with')}** ({opt.get('price'):,.0f} €): {bag}".replace(",", "."))
            st.caption(
                "Gepäckangaben pro Person, direkt aus den Buchungsoptionen. "
                "Läuft wöchentlich für das jeweils beste Angebot — die normale "
                "Suchantwort enthält keine Gepäckinformation."
            )


# ------------------------------------------------------------------- Fußzeile

health = analytics.data_health(snapshots)
cols = st.columns([2, 1, 1])
cols[0].caption(f"Letzte Messung: {health['last_run']} UTC · Build {BUILD}")
cols[1].caption(f"Erfolgreiche Messungen: {health['n_ok']}")
if health["n_failed"]:
    cols[2].error(f"{health['n_failed']} fehlgeschlagen: {health['errors'][:1]}")
else:
    cols[2].caption("Keine Fehler")
