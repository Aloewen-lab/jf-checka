# JF-Checka

Flugpreis-Monitoring **BER → Tokio** für die **Osterferien 2027**, für **5 Reisende**.
Streamlit-Dashboard mit Preisverlauf und Angebotstabelle, E-Mail-Alarme bei Preisverfall.

Konzept, Messwerte und Entscheidungsbegründungen: [PLAN.md](PLAN.md)

---

## Was das Ding macht

Dreimal täglich misst ein GitHub-Actions-Job die günstigsten Roundtrip-Preise für
16 Datumspaare rund um die Berliner Osterferien, schreibt sie als Parquet ins Repo
und verschickt Mails, wenn eine Regel greift. Das Dashboard liest dieselben Dateien.

Zwei Besonderheiten, die den Rest des Designs erklären:

- **Tokio wird als Stadt abgefragt** (`/m/07dfk`), nicht als Flughafen. Ein Call deckt
  damit Haneda (HND) *und* Narita (NRT) ab. `arrival_id=TYO` funktioniert nicht.
- **Die Gruppe darf sich aufteilen.** Gesucht wird primär für 5 Personen zusammen,
  regelmäßig zusätzlich für 3 + 2 getrennt. Maßgeblich ist `min(p5, p3 + p2)` —
  am 30.07.2026 waren das 7.230 € statt 7.620 €, also 390 € Unterschied.

## Setup

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # Werte eintragen, siehe unten
```

### Zugangsdaten

| Variable | Woher |
|---|---|
| `SERPAPI_KEY` | https://serpapi.com — Free-Plan, 250 Suchen/Monat |
| `SMTP_USER` | die absendende Gmail-Adresse |
| `SMTP_PASS` **oder** `SMTP_PASS_FILE` | Gmail-**App**-Passwort (nicht das Kontopasswort), https://myaccount.google.com/apppasswords, setzt 2FA voraus |

### Empfängerliste

Steht **nicht** in `config.yaml`, weil dieses Repo öffentlich ist und Adressen in
öffentlichen Repos abgeerntet werden:

```bash
cp recipients.example.yaml recipients.yaml   # gitignoriert, hier die echten Adressen
```

In GitHub Actions gibt es diese Datei nicht — dort kommt derselbe YAML-Text aus dem
Secret `JF_RECIPIENTS`. Ohne beides läuft der Collector weiter, verschickt aber
keine Mails und warnt im Log.

`SMTP_PASS_FILE` zeigt auf eine Datei, die nur das Passwort enthält — lokal der
bevorzugte Weg, damit das Geheimnis nicht in `.env` steht. In GitHub Actions gibt
es diese Datei nicht, dort wird `SMTP_PASS` als Secret gesetzt. Derselbe Code
deckt beides ab.

> Die Schlüsseldatei muss außerhalb des Repos liegen oder von `.gitignore`
> erfasst sein (`*-key.txt` ist drin). Ein `git add -A` erwischt sie sonst.

## Betrieb

```bash
.venv/bin/python collect.py --dry-run     # Plan zeigen, keine API-Calls
.venv/bin/python collect.py               # messen, speichern, Alarme prüfen
.venv/bin/python collect.py --no-alerts   # nur messen
.venv/bin/python collect.py --force-all   # Kadenz ignorieren
.venv/bin/python collect.py --max-calls 5 # Budget hart begrenzen

.venv/bin/python alerts.py --dry-run      # zeigt, was ausgelöst würde
.venv/bin/python alerts.py --test         # Testmail an alle Empfänger
.venv/bin/python alerts.py --digest       # Tagesübersicht sofort senden

.venv/bin/streamlit run app.py            # Dashboard auf :8501
```

Der Collector fragt vor jedem Lauf das echte SerpApi-Restkontingent ab und bricht
ab, statt ins Limit zu laufen. Bei knappem Budget gilt die Reihenfolge
`core` → Split-Trigger → `fringe` → Split-Routine → Referenzhistorie.

## Konfiguration

Alles in [config.yaml](config.yaml). Die wichtigsten Schrauben:

| Schlüssel | Wirkung |
|---|---|
| `dates.core` | Datumspaare mit täglicher Messung |
| `cadence.*_every_n_days` | Messintervalle. Alle auf `1` = volles Gitter täglich (braucht den 25-$-Plan) |
| `cadence.split_check_on_jump_pct` | löst sofort einen 3+2-Check aus, wenn der 5er-Preis so stark steigt |
| `split_min_saving_eur` | unter dieser Ersparnis wird Aufteilen nicht empfohlen |
| `quota.monthly_limit`, `quota.reserve_calls` | Budget-Guard |
| `recipients[].rules` | `absolute_below_eur`, `relative_drop_pct`, `new_all_time_low` |
| `recipients[].filters` | `max_stops`, `max_duration_h`, `min_nights`, `max_nights` |

Alle Preise in der Config sind **Gruppen-Gesamtpreise** für 5 Personen.

## Deployment

**Collector:** `.github/workflows/collect.yml`, Cron 05:00 / 07:00 / 15:00 UTC.
Repository-Secrets: `SERPAPI_KEY`, `SMTP_USER`, `SMTP_PASS`, `JF_RECIPIENTS`.
Der Job committet neue Parquet-Dateien selbst zurück und braucht daher
`permissions: contents: write` (ist gesetzt).

**Dashboard:** Streamlit Community Cloud auf dasselbe Repo, Entry Point `app.py`.
Bei jedem Daten-Commit deployt Streamlit automatisch neu.

## Datenmodell

```
data/offers/dt=YYYY-MM-DD/*.parquet          # einzelne Angebote
data/snapshots/dt=YYYY-MM-DD/*.parquet       # jeder Messversuch, auch Fehler
data/price_history/dt=YYYY-MM-DD/*.parquet   # Googles Preisgraph, wenn geliefert
data/collector_state.json                    # Kadenz: was wurde wann gemessen
data/alert_state.json                        # Dedup: letzte Alarmpreise, Digests
```

Append-only: jeder Lauf schreibt neue Dateien, nichts wird überschrieben. Dadurch
gibt es keine Binär-Diffs in Git und die Historie bleibt unveränderlich.

`snapshots` ist nicht redundant — nur damit lässt sich eine Lücke in der Zeitreihe
(API-Fehler) von einem unveränderten Preis unterscheiden.

## Grenzen

- **Keine Rückschau vor dem 30.07.2026.** Googles Preisgraph existiert für
  Ostern 2027 noch nicht, er entsteht erst bei näheren Terminen. Der Collector
  speichert ihn automatisch, sobald er auftaucht. Als Kontext läuft wöchentlich ein
  Referenztermin auf derselben Strecke mit — das ist das Routen-Preisniveau,
  **nicht** der Ostern-Preis.
- **Gepäck:** gesucht wird mit 1 aufgegebenem Gepäckstück pro Person (`bags=1`,
  seit 30.07.2026 — davor ohne, `bags`-Spalte im Schema unterscheidet beides).
  Ob ein konkretes Angebot Freigepäck enthält, verrät nur das wöchentliche
  Gepäck-Audit (`python audit.py`), nicht die Suchantwort.
  Sitzplatzreservierung ist nie enthalten.
- **Die 2er-Messung ist volatil.** Dass 2 Passagiere pro Person günstiger sein
  können als 1, ist ungewöhnlich; ob der Effekt stabil ist, zeigt die Zeitreihe.
- **Datenquelle ist austauschbar, aber nicht garantiert.** Die Amadeus
  Self-Service API wurde am 17.07.2026 abgeschaltet; deshalb liegt SerpApi hinter
  einem schmalen `Provider`-Interface.
