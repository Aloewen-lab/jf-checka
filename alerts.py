"""Alarmregeln und Versand.

Rechnet auf denselben Aggregaten wie das Dashboard (`analytics`), damit ein Alarm
nie eine Zahl behauptet, die in der App nicht steht.

Jeder Empfänger hat eigene Filter — wer nur Direktflüge will, bekommt auch nur
für Direktflüge Alarme. Deshalb wird der Gruppenpreis pro Empfänger neu berechnet
und nicht der globale Wert versendet.

Aufruf:
    python alerts.py --test            # Testmail an alle Empfänger
    python alerts.py --dry-run         # zeigt, was ausgelöst würde
    python alerts.py                   # prüft und versendet
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
from dotenv import load_dotenv

import analytics
import store
from analytics import Filters, Kpis
from notify import SmtpConfigError, send

ROOT = pathlib.Path(__file__).resolve().parent
STATE_FILE = store.BASE / "alert_state.json"
RECIPIENTS_FILE = ROOT / "recipients.yaml"
MIN_IMPROVEMENT_EUR = 1.0
# Ein deutlicher Absturz durchbricht den Cooldown — 12 h zu warten, während der
# Preis vielleicht schon wieder steigt, wäre der falsche Kompromiss.
COOLDOWN_OVERRIDE_FACTOR = 0.95


# ------------------------------------------------------------------- Zustand


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"alerts": {}, "digests": {}}
    data = json.loads(STATE_FILE.read_text())
    data.setdefault("alerts", {})
    data.setdefault("digests", {})
    return data


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


# -------------------------------------------------------------------- Modell


@dataclass
class Alert:
    recipient: str
    signature: str
    reasons: list[str]
    price: float
    pair: str
    source: str
    kpis: Kpis
    offers: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def subject(self) -> str:
        out, ret = self.pair.split(" → ")
        return (
            f"[JF-Checka] {_eur(self.price)} — {_de(out)} → {_de(ret)} "
            f"({self.source})"
        )


def _eur(value: float) -> str:
    return f"{value:,.0f} €".replace(",", ".")


def _de(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day:02d}.{d.month:02d}."


# --------------------------------------------------------------- Auswertung


def load_recipients() -> list[dict]:
    """Empfängerliste aus recipients.yaml oder dem Secret JF_RECIPIENTS.

    Bewusst nicht in config.yaml: das Repo ist öffentlich, und E-Mail-Adressen in
    öffentlichen Repos werden abgeerntet. Lokal die Datei, in GitHub Actions das
    Secret — dieselbe Struktur, zwei Quellen.
    """
    raw = os.environ.get("JF_RECIPIENTS", "").strip()
    source = "JF_RECIPIENTS"
    if not raw:
        if not RECIPIENTS_FILE.exists():
            print(
                "  WARNUNG: keine Empfänger gefunden. recipients.yaml anlegen "
                "(Vorlage: recipients.example.yaml) oder JF_RECIPIENTS setzen.",
                file=sys.stderr,
            )
            return []
        raw = RECIPIENTS_FILE.read_text()
        source = str(RECIPIENTS_FILE.name)

    data = yaml.safe_load(raw)
    entries = data if isinstance(data, list) else (data or {}).get("recipients") or []
    if not entries:
        print(f"  WARNUNG: {source} enthält keine Empfänger.", file=sys.stderr)
    return entries


def resolve(cfg: dict) -> dict:
    """Config mit der extern gehaltenen Empfängerliste zusammenführen."""
    if not cfg.get("recipients"):
        cfg["recipients"] = load_recipients()
    return cfg


def _window(cfg: dict, offers: pd.DataFrame) -> pd.DataFrame:
    """Nur die konfigurierten Ostern-Termine. Der Referenztermin aus
    `reference_history` liegt in 2026 und ist kein Alarmgegenstand."""
    if offers.empty:
        return offers
    win_out = set(cfg["dates"]["outbound"])
    win_in = set(cfg["dates"]["inbound"])
    return offers[
        offers["outbound_date"].isin(win_out) & offers["return_date"].isin(win_in)
    ]


def recipient_filters(rec: dict) -> Filters:
    f = rec.get("filters") or {}
    return Filters(
        max_stops=f.get("max_stops"),
        max_duration_h=f.get("max_duration_h"),
        min_nights=f.get("min_nights"),
        max_nights=f.get("max_nights"),
    )


def evaluate(cfg: dict, offers: pd.DataFrame) -> list[Alert]:
    """Prüft alle Regeln je Empfänger. Reine Funktion — kein Versand, kein State."""
    alerts: list[Alert] = []
    primary = cfg["passenger_configs"]["primary"]
    split = tuple(cfg["passenger_configs"]["split"])

    for rec in cfg.get("recipients") or []:
        email = rec["email"]
        rules = rec.get("rules") or {}

        subset = analytics.apply_filters(offers, recipient_filters(rec))
        if subset.empty:
            continue

        group_df = analytics.group_prices(
            analytics.per_config_daily_min(subset), primary=primary, split=split
        )
        daily = analytics.daily_best(group_df)
        if daily.empty:
            continue
        k = analytics.kpis(daily)
        if k.current is None:
            continue

        reasons: list[str] = []

        threshold = rules.get("absolute_below_eur")
        if threshold is not None and k.current < float(threshold):
            reasons.append(
                f"unter deiner Schwelle von {_eur(float(threshold))}"
            )

        drop_pct = rules.get("relative_drop_pct")
        if drop_pct is not None and len(daily) >= 2:
            median = float(analytics.rolling_median(daily).iloc[-1])
            if k.current <= median * (1 - float(drop_pct) / 100):
                reasons.append(
                    f"{(1 - k.current / median) * 100:.0f} % unter dem "
                    f"7-Tage-Median ({_eur(median)})"
                )

        if rules.get("new_all_time_low") and k.is_new_low:
            reasons.append("neues Allzeit-Tief seit Tracking-Start")

        if not reasons:
            continue

        latest_day = group_df["day"].max()
        best = subset[subset["ts_utc"].str.slice(0, 10) == latest_day]

        alerts.append(
            Alert(
                recipient=email,
                signature=f"{email}|{k.current_pair}",
                reasons=reasons,
                price=k.current,
                pair=str(k.current_pair),
                source=str(k.current_source),
                kpis=k,
                offers=best.nsmallest(3, "price_per_person_eur"),
            )
        )
    return alerts


def should_send(alert: Alert, state: dict, cooldown_hours: int, now: datetime) -> bool:
    """Nur bei echter Verbesserung, und nicht häufiger als der Cooldown erlaubt.

    Ohne diese Bremse würde jeder Lauf denselben Preis erneut melden — bei
    E-Mail als einzigem Kanal wäre das der schnellste Weg in den Spam-Ordner.
    """
    previous = state["alerts"].get(alert.signature)
    if previous is None:
        return True

    prev_price = float(previous["price"])
    if alert.price > prev_price - MIN_IMPROVEMENT_EUR:
        return False

    elapsed = now - datetime.fromisoformat(previous["ts"])
    if elapsed >= timedelta(hours=cooldown_hours):
        return True
    return alert.price <= prev_price * COOLDOWN_OVERRIDE_FACTOR


# ------------------------------------------------------------------ Textbau


def _offer_lines(alert: Alert, group_size: int) -> list[str]:
    lines = []
    for _, o in alert.offers.iterrows():
        dur = int(o["duration_out_min"])
        lines.append(
            f"  {_eur(float(o['price_eur']))} für {int(o['adults'])} Pers. "
            f"({_eur(float(o['price_per_person_eur']))} p. P.) · "
            f"{o['airlines']} · {o['arrival_airport']} · "
            f"{int(o['stops_out'])} Stopp(s) · {dur // 60}h{dur % 60:02d}"
        )
    return lines


def render(alert: Alert, cfg: dict) -> tuple[str, str]:
    group_size = cfg["group_size"]
    k = alert.kpis
    out, ret = alert.pair.split(" → ")

    text = [
        f"Günstigster Preis für {group_size} Personen: {_eur(alert.price)}",
        f"Termin: {out} → {ret}  ({_eur(alert.price / group_size)} pro Person)",
        f"Buchungsart: {alert.source}",
        "",
        "Ausgelöst weil: " + "; ".join(alert.reasons),
        "",
    ]
    if k.delta_prev_day is not None:
        text.append(f"Gegenüber Vortag: {k.delta_prev_day:+,.0f} €".replace(",", "."))
    if k.all_time_low is not None:
        text.append(f"Allzeit-Tief: {_eur(k.all_time_low)} am {k.all_time_low_day}")
    text.append(f"Tage bis Abflug: {k.days_to_departure}")

    if not alert.offers.empty:
        text += ["", "Günstigste Einzelangebote:"] + _offer_lines(alert, group_size)
        link = str(alert.offers.iloc[0]["deep_link"])
        text += ["", f"Bei Google Flights ansehen: {link}"]

    text += [
        "",
        "Hinweis: Preise ohne Gepäck und Sitzplatzreservierung.",
        "Dashboard: streamlit run app.py",
    ]
    plain = "\n".join(text)

    rows = "".join(
        f"<tr><td style='padding:4px 10px 4px 0'>{_eur(float(o['price_eur']))}</td>"
        f"<td style='padding:4px 10px 4px 0'>{int(o['adults'])} Pers.</td>"
        f"<td style='padding:4px 10px 4px 0'>{o['airlines']}</td>"
        f"<td style='padding:4px 10px 4px 0'>{o['arrival_airport']}</td>"
        f"<td style='padding:4px 10px 4px 0'>{int(o['stops_out'])} Stopp(s)</td>"
        f"<td style='padding:4px 0'>{int(o['duration_out_min']) // 60}h"
        f"{int(o['duration_out_min']) % 60:02d}</td></tr>"
        for _, o in alert.offers.iterrows()
    )
    link = (
        str(alert.offers.iloc[0]["deep_link"]) if not alert.offers.empty else ""
    )
    html = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#0b0b0b;max-width:640px">
  <p style="font-size:13px;color:#52514e;margin:0 0 4px">JF-Checka · BER → Tokio</p>
  <p style="font-size:28px;font-weight:600;margin:0">{_eur(alert.price)}
     <span style="font-size:14px;font-weight:400;color:#52514e">
       für {group_size} Personen · {_eur(alert.price / group_size)} p. P.</span></p>
  <p style="margin:6px 0 16px;font-size:15px">{out} → {ret} · {alert.source}</p>
  <p style="background:#eef6ee;border-radius:8px;padding:10px 12px;margin:0 0 16px;font-size:14px">
     <b>Ausgelöst weil:</b> {"; ".join(alert.reasons)}</p>
  <table style="font-size:13px;border-collapse:collapse">{rows}</table>
  <p style="margin:18px 0 0"><a href="{link}"
     style="background:#2a78d6;color:#fff;text-decoration:none;padding:9px 16px;
     border-radius:6px;font-size:14px;display:inline-block">Bei Google Flights ansehen</a></p>
  <p style="font-size:12px;color:#83827d;margin:18px 0 0">
     Preise ohne Gepäck und Sitzplatzreservierung · Tage bis Abflug:
     {k.days_to_departure}</p>
</div>"""
    return plain, html


# ------------------------------------------------------------------- Versand


def dispatch(cfg: dict, dry_run: bool = False) -> int:
    cfg = resolve(cfg)
    offers = store.read_offers()
    if offers.empty:
        print("Keine Angebotsdaten — nichts zu prüfen.")
        return 0

    offers = _window(cfg, offers)
    state = load_state()
    now = datetime.now(timezone.utc)
    cooldown = int((cfg.get("alerts") or {}).get("cooldown_hours", 12))

    sent = maybe_send_digest(cfg, offers, state, now)

    candidates = evaluate(cfg, offers)
    if not candidates:
        print("Keine Regel ausgelöst.")
        save_state(state)
        return sent

    for alert in candidates:
        if not should_send(alert, state, cooldown, now):
            print(f"  übersprungen (Dedup/Cooldown): {alert.recipient} {alert.pair}")
            continue

        plain, html = render(alert, cfg)
        if dry_run:
            print(f"\n--- würde senden an {alert.recipient} ---")
            print(alert.subject)
            print(plain)
            continue

        try:
            send(alert.recipient, alert.subject, plain, html)
        except Exception as exc:  # Versandfehler darf den Lauf nicht abbrechen
            print(f"  FEHLER Versand an {alert.recipient}: {exc}", file=sys.stderr)
            continue

        state["alerts"][alert.signature] = {
            "price": alert.price,
            "ts": now.isoformat(timespec="seconds"),
        }
        sent += 1
        print(f"  gesendet an {alert.recipient}: {alert.subject}")

    if not dry_run:
        save_state(state)
    return sent


def maybe_send_digest(
    cfg: dict, offers: pd.DataFrame, state: dict, now: datetime, force: bool = False
) -> int:
    """Tages-Digest an Empfänger mit `digest: true`.

    Wichtig, weil E-Mail der einzige Kanal ist: ohne ein tägliches Lebenszeichen
    ist ein stiller Collector-Ausfall von "Preis unverändert" nicht zu
    unterscheiden — man würde schlicht nie wieder etwas hören und es für gute
    Nachrichten halten.
    """
    conf = cfg.get("alerts") or {}
    local = now.astimezone(ZoneInfo("Europe/Berlin"))
    today_local = local.date().isoformat()
    if not force and local.hour < int(conf.get("digest_hour_local", 8)):
        return 0

    group_df = analytics.group_prices(
        analytics.per_config_daily_min(offers),
        primary=cfg["passenger_configs"]["primary"],
        split=tuple(cfg["passenger_configs"]["split"]),
    )
    daily = analytics.daily_best(group_df)
    if daily.empty:
        return 0
    k = analytics.kpis(daily)
    latest_day = group_df["day"].max()
    top = group_df[group_df["day"] == latest_day].nsmallest(3, "group_price")
    health = analytics.data_health(store.read_snapshots())
    min_saving = float(cfg.get("split_min_saving_eur", 0))
    group_size = cfg["group_size"]

    lines = [
        f"Stand {today_local} · BER → Tokio · {group_size} Personen",
        "",
        f"Günstigster Gruppenpreis: {_eur(k.current)}"
        f"  ({_eur(k.current / group_size)} p. P.)",
        f"Termin: {k.current_pair} · {k.current_source}",
    ]
    if k.delta_prev_day is not None:
        lines.append(f"Gegenüber Vortag: {k.delta_prev_day:+,.0f} €".replace(",", "."))
    lines += [
        f"Allzeit-Tief: {_eur(k.all_time_low)} am {k.all_time_low_day}",
        f"Tage bis Abflug: {k.days_to_departure} · Historie: {k.n_days_tracked} Tag(e)",
        "",
        "Beste Termine heute:",
    ]
    for _, r in top.iterrows():
        note = ""
        saving = r["split_saving"]
        if pd.notna(saving) and saving >= min_saving:
            note = f"  (getrennt buchen spart {_eur(float(saving))})"
        lines.append(
            f"  {r['outbound_date']} → {r['return_date']}  {int(r['nights'])}N  "
            f"{_eur(float(r['group_price']))}{note}"
        )
    lines += [
        "",
        f"Datenlage: {health['n_ok']} erfolgreiche Messungen, "
        f"{health['n_failed']} Fehler · letzte {health['last_run']} UTC",
        "",
        "Diese Mail kommt täglich, auch ohne Preisänderung — bleibt sie aus,",
        "läuft der Collector nicht mehr.",
    ]
    plain = "\n".join(lines)

    sent = 0
    for rec in cfg.get("recipients") or []:
        if not rec.get("digest"):
            continue
        email = rec["email"]
        if not force and state["digests"].get(email) == today_local:
            continue
        try:
            send(email, f"[JF-Checka] Tagesübersicht {today_local}", plain)
        except Exception as exc:
            print(f"  FEHLER Digest an {email}: {exc}", file=sys.stderr)
            continue
        state["digests"][email] = today_local
        sent += 1
        print(f"  Digest an {email} verschickt")
    return sent


def send_test(cfg: dict) -> int:
    """Testmail an alle Empfänger — prüft SMTP-Zugang und Formatierung."""
    cfg = resolve(cfg)
    offers = _window(cfg, store.read_offers())
    group_df = analytics.group_prices(
        analytics.per_config_daily_min(offers),
        primary=cfg["passenger_configs"]["primary"],
        split=tuple(cfg["passenger_configs"]["split"]),
    )
    daily = analytics.daily_best(group_df)
    k = analytics.kpis(daily)
    latest_day = group_df["day"].max()

    probe = Alert(
        recipient="",
        signature="test",
        reasons=["Testmail — keine echte Regel ausgelöst"],
        price=float(k.current),
        pair=str(k.current_pair),
        source=str(k.current_source),
        kpis=k,
        offers=offers[offers["ts_utc"].str.slice(0, 10) == latest_day].nsmallest(
            3, "price_per_person_eur"
        ),
    )
    plain, html = render(probe, cfg)

    sent = 0
    for rec in cfg.get("recipients") or []:
        probe.recipient = rec["email"]
        send(rec["email"], "[JF-Checka] Testmail — Setup funktioniert", plain, html)
        print(f"  Testmail an {rec['email']} verschickt")
        sent += 1
    return sent


def main() -> int:
    ap = argparse.ArgumentParser(description="JF-Checka Alarme")
    ap.add_argument("--test", action="store_true", help="Testmail verschicken")
    ap.add_argument("--dry-run", action="store_true", help="nur zeigen, nicht senden")
    ap.add_argument(
        "--digest", action="store_true", help="Digest sofort senden, ohne Uhrzeitprüfung"
    )
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())

    try:
        if args.test:
            n = send_test(cfg)
            print(f"\n{n} Testmail(s) verschickt.")
            return 0
        if args.digest:
            cfg = resolve(cfg)
            offers = _window(cfg, store.read_offers())
            state = load_state()
            n = maybe_send_digest(
                cfg, offers, state, datetime.now(timezone.utc), force=True
            )
            save_state(state)
            print(f"\n{n} Digest(s) verschickt.")
            return 0
        n = dispatch(cfg, dry_run=args.dry_run)
        print(f"\n{n} Alarm(e) verschickt.")
        return 0
    except SmtpConfigError as exc:
        print(f"SMTP-Konfiguration unvollständig: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
