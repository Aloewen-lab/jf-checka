# JP-Flightwatch — Plan

Flugpreis-Monitoring **BER → Japan** für die **Osterferien 2027**, mit Streamlit-Dashboard
(Preisverlauf + aktuelle Angebote) und E-Mail-Alarmen an einen definierten Empfängerkreis.

Stand: 2026-07-30

---

## 0. Getroffene Entscheidungen

| Thema | Entscheidung |
|---|---|
| Datenquelle | **SerpApi Google Flights**, Free-Tier (250 Suchen/Monat), Upgrade auf $25/Monat später möglich |
| Alarmkanal | **E-Mail (SMTP)** — einziger Kanal, kein WhatsApp/Telegram |
| Betrieb | **GitHub Actions Cron** (Collector) + **Streamlit Community Cloud** (Dashboard) |
| Reisende | **5 Personen** (Default: 5 Erwachsene; Kinderaufteilung s. §1) |
| Ziel | TYO (NRT + HND); OSA/NGO/FUK optional nach Upgrade |

**Wichtiger Kontext:** Die Amadeus Self-Service API — der übliche Default für Flugpreis-Projekte —
wurde am 17.07.2026 abgeschaltet (https://www.phocuswire.com/amadeus-shut-down-self-service-apis-portal-developers).
Deshalb die Provider-Abstraktion in §3: die Quelle muss austauschbar sein.

---

## 1. Suchraum

Osterferien Berlin 2027: **Mo 22.03. – Fr 02.04.2027** (Ostersonntag 28.03.,
Karfreitag 26.03., Ostermontag 29.03.).
Quelle: https://www.schulferien.org/deutschland/ferien/berlin/

Für eine 2-Wochen-Reise zählen die einrahmenden Wochenenden:

- **Hinflug:** 19.–22.03.2027 (4 Tage)
- **Rückflug:** 02.–05.04.2027 (4 Tage)
- **Aufenthalt:** 13–16 Nächte
- **Gitter:** 16 Datumskombinationen

Fares für März 2027 sind bereits im Verkauf (~240 Tage Vorlauf) — Tracking kann sofort starten.

### 5 Erwachsene, aufteilbar auf 2 + 3

Alle 5 sind Erwachsene (keine Kinderfares). Die Gruppe darf **getrennt fliegen: eine Buchung
für 3, eine für 2**.

**Warum getrennt buchen überhaupt helfen kann:** Airlines verkaufen in Fare-Buckets mit
begrenzter Sitzzahl, und eine Suche für N Passagiere liefert nur Angebote, bei denen
**alle N Plätze im selben Bucket** frei sind. Sind im billigsten Bucket noch 3 Sitze,
zeigt eine 5er-Suche diesen Preis nicht — eine 3er-Suche schon.

**Messungen vom 30.07.2026** (BER → Tokio, Economy), Datumspaar 20.03. → 03.04.2027:

| Passagiere | Gesamtpreis | pro Person |
|---|---|---|
| 1 | 1.524 € | 1.524 € |
| 2 | **2.658 €** | **1.329 €** |
| 3 | 4.572 € | 1.524 € |
| 5 | 7.620 € | 1.524 € |

Die 1er-, 3er- und 5er-Suche sind exakt proportional — hier gibt es keinen Aufschlag.
Die **2er-Suche fällt aus dem Muster**: 1.329 € p. P. statt 1.524 €. Damit gilt

```
3 + 2 getrennt = 4.572 + 2.658 = 7.230 €   gegenüber   5 zusammen = 7.620 €
                                                       Ersparnis:      390 €
```

**Getrennt buchen spart auf dem besten Datumspaar also 390 €** — die Aufteilung ist
kein theoretischer Vorteil, sondern messbar. Auf den drei anderen Kern-Paaren war der
2er-Preis dagegen proportional (3.048 € = 1.524 € p. P.), die Ersparnis dort also 0 €.
Die Ersparnis ist damit **paar-spezifisch und muss laufend gemessen werden**, sie lässt
sich nicht einmalig feststellen.

Einschränkung: dass 2 Passagiere pro Person günstiger sind als 1 Passagier, ist ungewöhnlich
und kann auch Volatilität zwischen zwei Calls sein (die Messungen lagen Minuten auseinander).
Ob der Effekt stabil ist, zeigt erst die Zeitreihe über einige Tage.

Daraus folgt die Suchstrategie:

1. **Primär `adults: 5`** — ein Call pro Datumspaar, der ehrliche „alle zusammen"-Preis.
2. **Split-Kontrolle `adults: 3` + `adults: 2`** auf den Kern-Paaren, regelmäßig *und*
   ereignisgesteuert: springt der 5er-Preis um >10 %, läuft sofort ein Split-Check.
3. **Gruppenpreis** = `min(p5, p3 + p2)` — die Kennzahl für Alarme und Dashboard.
   Liegt der Split darunter, zeigt das Dashboard „getrennt buchen spart X €".
4. **Verfügbarkeit ist ein eigenes Signal.** Ein Preissprung heißt bei Gruppen meist
   „Bucket leer", nicht „Airline hat erhöht" — `snapshots` loggt die Angebotsanzahl mit.
5. **Schwellwerte sind Gruppen-Gesamtpreise**, nicht pro Person. Das Dashboard zeigt beides.

### Tokio als Stadt statt als Flughafen

`arrival_id=TYO` wird von der API **nicht** akzeptiert (`"Google Flights hasn't returned any
results for this query"`). Stattdessen funktioniert der Google-Knowledge-Graph-Code der Stadt:

```
arrival_id = /m/07dfk        # Tokio, liefert HND und NRT in EINEM Call
```

Verifiziert: die Antwort enthält Angebote mit Ankunft an beiden Flughäfen. Das halbiert die
Calls gegenüber getrennten NRT- und HND-Suchen — und HND ist wichtig, weil dort inzwischen
viele Langstrecken aus Europa landen. Für weitere Ziele muss der jeweilige Stadt-Code
einmalig ermittelt werden.

### Quota-Staffelung

1 Roundtrip-Suche für *eine* Passagierzahl = 1 API-Call, und dieser Call deckt dank des
Stadt-Codes bereits **HND + NRT gemeinsam** ab. Volles Gitter mit `adults: 5` täglich wären
16 × 30 = 480/Monat — noch zu viel für den Free-Tier (250/Monat, Stand 30.07.2026:
248 verbleibend, Reset am 30. des Monats).

**Variante A — Free-Tier (250/Monat), gestaffelt:**

| Gruppe | Paare | Konfig | Kadenz | Calls/Monat |
|---|---|---|---|---|
| Kern (4 plausibelste Datumspaare) | 4 | 5er | täglich | 120 |
| Rand | 12 | 5er | jeden 4. Tag | 90 |
| Split-Kontrolle (Kern) | 4 | 3er + 2er | wöchentlich | 32 |
| **Summe** | | | | **~242** |

Knapp, aber tragfähig. Das ereignisgesteuerte Split-Check-Budget muss aus dem Rand-Kontingent
genommen werden — der Quota-Guard priorisiert in dieser Reihenfolge: Kern > Split-Trigger >
Rand.

**Variante B — Starter-Plan, $25/Monat (1.000 Calls):**
volles Gitter täglich (480) + wöchentliche Split-Kontrolle auf allen 16 Paaren (128) = 608,
plus Reserve für Osaka als zweites Ziel. Keine Staffelungslogik, keine Priorisierung.

**Empfehlung: Variante B.** Bei einer Reise um 7.600 € sind 25 €/Monat für eine lückenlose
Zeitreihe die bessere Entscheidung, und sie macht die Priorisierungslogik überflüssig.
Der Collector wird so gebaut, dass **nur die Kadenz in `config.yaml`** umgestellt wird —
A und B sind derselbe Code, also ist der Start mit A und ein späteres Upgrade kostenfrei.

Harter Monatszähler in `collect.py`: bei Erreichen des Limits **abbrechen**, nicht weiterlaufen.
Das echte Restkontingent ist über `https://serpapi.com/account` kostenlos abfragbar und wird
vor jedem Lauf geprüft.

---

## 2. Datenquellen

Ein `Provider`-Interface, mehrere Implementierungen — jede Quelle in diesem Markt ist fragil.

| Provider | Kosten | Daten | Rolle |
|---|---|---|---|
| SerpApi Google Flights | 250/Mon. gratis, dann $25/1.000 | live, EUR erzwingbar | **Primär** |
| fast_flights (`AWeirdDev/flights`) | gratis | live, Protobuf-Scraper für Google Flights | Fallback / Quota-Schoner, kann brechen |
| Travelpayouts Flight Data | gratis (Affiliate) | *gecachte* Fares, Monats-Matrix | Preisniveau-Baseline vor Tracking-Start |

Bewusst nicht: Amadeus (abgeschaltet), Kiwi/Skyscanner (gated, 50k+ MAU),
Airline-Seiten direkt (ToS).

**Rechtlich:** SerpApi ist der saubere Weg zu Google-Flights-Daten. Direktes Scraping von
Airline- oder OTA-Seiten ist ToS-widrig und nicht Teil dieses Projekts.

---

## 3. Architektur

```
jp-flightwatch/
  config.yaml                    # Fenster, Gitter, Kadenz, Empfänger, Alarmregeln
  models.py                      # Offer, SearchRequest (dataclasses)
  providers/
    base.py                      # Provider-Protokoll
    serpapi.py                   # primär
    fast_flights.py              # Fallback
    travelpayouts.py             # Baseline
  store.py                       # Parquet-Writer + Aggregationen
  collect.py                     # CLI: suchen -> speichern -> Alarme prüfen
  alerts.py                      # Regel-Engine + Dedup-State
  notify/
    email.py                     # SMTP
  app.py                         # Streamlit-Dashboard
  data/
    offers/dt=2026-07-30/snap-*.parquet
    alert_state.json             # Dedup: letzte Alarmpreise je Signatur
  .github/workflows/collect.yml
  requirements.txt
  README.md
```

### Storage

Append-only **Parquet, partitioniert nach Tag**. Kein Binär-Churn in Git (jeder Lauf schreibt
eine *neue* Datei), direkt per Glob mit pandas/duckdb lesbar. Migrationspfad zu
Neon/Supabase-Postgres bleibt offen, falls es wächst.

**`offers`**
`ts_utc, provider, origin, dest, out_date, ret_date, price_eur, airlines, stops_out,
stops_ret, duration_out_min, duration_ret_min, cabin, deep_link, raw_json`

**`snapshots`**
`ts, provider, params_hash, n_offers, status, error`

Der zweite Table ist nicht optional: nur damit lässt sich eine **Lücke in der Zeitreihe**
(API-Fehler) von einer **echten Preisänderung** unterscheiden.

### Antwortstruktur der API (verifiziert 30.07.2026)

Relevant für den Parser in `providers/serpapi.py`:

- Angebote liegen in **zwei** Listen: `best_flights` (3 Stück) und `other_flights` (8) —
  beide müssen gelesen werden, das Minimum kann in `other_flights` liegen.
- `price` ist der **Gesamtpreis der Roundtrip-Buchung für alle Passagiere** in EUR.
- Bei `type=1` (Roundtrip) enthält `flights` nur die **Hinflug-Legs**. Der Preis ist trotzdem
  der vollständige Roundtrip-Preis. Für Rückflugdetails wäre ein Zweitcall mit
  `departure_token` nötig — **für das Monitoring nicht erforderlich**, das spart die Hälfte
  der Calls.
- `booking_token` fehlt daher im ersten Call. Der Deep-Link wird stattdessen als
  Google-Flights-URL aus den Suchparametern konstruiert; zum Buchen klickt man dort weiter.
- `price_insights` liefert gratis `lowest_price`, `price_level` ("typical"/"low"/"high") und
  `typical_price_range` — Googles eigene Einordnung, die als zweite Alarm-Dimension taugt
  („price_level ist von typical auf low gesprungen").
- Nutzbare Felder pro Angebot: `price, type, total_duration, flights[], layovers[],
  carbon_emissions, airline_logo`. Pro Leg: `airline, flight_number, airplane, travel_class,
  legroom, duration, departure_airport{id,time}, arrival_airport{id,time}, extensions`.

---

## 4. Streamlit-Dashboard

- **KPI-Zeile:** günstigster Gesamtpreis jetzt (mit „= X € p. P." darunter) · Δ zu gestern ·
  Δ zum 7-Tage-Median · Allzeit-Tief (+ Datum) · Tage bis Abflug
- **Umschalter Gesamt / pro Person** global in der Sidebar — wirkt auf alle Charts und die Tabelle
- **Preisverlauf** (Kernanforderung): Tagesminimum ab Tracking-Start als Linie,
  7-Tage-Rolling-Median als Band, Marker bei neuen Tiefstwerten.
  Umschaltbar auf „nur Direktflüge" oder eine einzelne Datumskombination.
- **Fare-Grid-Heatmap:** Hinflugdatum × Rückflugdatum, Farbe = aktuell günstigster Preis
  → zeigt sofort das beste Datumspaar.
- **Angebotstabelle:** aktuelle Angebote, sortier- und filterbar (max. Stopps, Airline,
  max. Reisedauer, Datum), mit Deep-Link zur Buchung.
- **Verteilung:** Boxplot der Tagespreise — macht sichtbar, ob *ein* Ausreißer billig ist
  oder das Niveau insgesamt fällt. *(noch offen)*

### Umsetzungshinweise (M2, erledigt)

- Alle Kennzahlen rechnen auf `offers`, nicht auf `snapshots` — nur so wirken die Filter
  ("max. 1 Stopp") konsistent auf KPIs, Verlauf, Heatmap und Tabelle. Die Aggregate liegen
  in `analytics.py`, damit die Alarmregeln in M3 exakt dieselben Zahlen benutzen und nicht
  auf einer zweiten Implementierung auseinanderlaufen.
- Farben aus der validierten Referenzpalette gegen die Oberfläche `#fcfcfb` geprüft
  (kategoriale Slots 1+2: normal ΔE 33.6, protan 24.7, alle Checks PASS). Das Streamlit-Theme
  ist deshalb fest auf hell gesetzt — ein automatischer Dark-Flip wäre nicht validiert.
- Deltas tragen Icon + Wort ("▼ günstiger"), nie Farbe allein: die Statusfarben der Palette
  liegen im hellen Modus teils unter 3:1 Kontrast.
- Heatmap-Achsen **müssen** `type="category"` sein. Als Zeitachse interpretiert Plotly die
  Datums-Strings und verschiebt die Zellen gegen ihre Labels — der Grid zeigte dadurch
  zunächst den 18.03. statt des 19.03.
- "Aktuelle Angebote" heißt: jüngste Messung **je Datumspaar und Passagierzahl**. Über den
  ganzen Tag zu filtern würde mehrere Läufe vermischen und überholte Preise als aktuell
  ausgeben.

---

## 5. Alarmierung (E-Mail)

**Transport:** SMTP über Gmail mit App-Passwort (setzt 2FA am Google-Konto voraus),
`smtp.gmail.com:587` + STARTTLS. Zugangsdaten als GitHub Secrets `SMTP_USER` / `SMTP_PASS`.

**Zwei Mail-Typen:**

1. **Sofort-Alarm** — nur bei ausgelöster Regel, Subject enthält Preis und Datumspaar,
   z. B. `[JP] 712 € — 20.03. → 03.04. (1 Stopp, 14h)`. Body: Top-3-Angebote + Deep-Links.
2. **Tages-Digest** (optional, 1×/Tag) — kurzer Überblick auch ohne Trigger, damit klar ist,
   dass das System läuft. Ein stiller Collector-Ausfall ist sonst nicht zu bemerken.

**Regeln, pro Empfänger in `config.yaml`:**

Alle `*_eur`-Werte sind **Gruppen-Gesamtpreise für 5 Reisende**.
Referenz aus dem Smoke-Test: 7.620 € gesamt = 1.524 € p. P., von Google als
`price_level: "typical"` eingeordnet (typische Spanne 3.150–5.400 € für 3 Pax,
also ~1.050–1.800 € p. P.).

```yaml
recipients:
  - email: person.a@example.com
    rules:
      absolute_below_eur: 6500     # Gruppe gesamt, ~1.300 EUR p. P.
      relative_drop_pct: 10        # <= 90 % des 7-Tage-Medians
      new_all_time_low: true
    filters:
      max_stops: 2
    digest: true
  - email: person.b@example.com
    rules:
      absolute_below_eur: 6000
    filters:
      max_stops: 1
      max_duration_h: 18
    digest: false
```

Die `absolute_below_eur`-Werte sind aus der ersten Messung abgeleitet, nicht aus Historie.
Nach ~10 Tagen Tracking sollten sie einmal nachgezogen werden — bis dahin tragen
`relative_drop_pct` und `new_all_time_low` die Alarmierung, weil sie kein Vorwissen über
das Niveau brauchen.

**Anti-Spam** (bei E-Mail als einzigem Kanal besonders wichtig):
Alarm nur bei *Verbesserung* gegenüber dem letzten Alarm derselben Signatur
(Route + Datumspaar + Empfänger), plus Cooldown von 12 h. State in `data/alert_state.json`,
wird vom Actions-Workflow mitcommittet.

**Deliverability:** eine Mail pro Empfänger (nicht BCC-Sammelmail), damit die Regeln
individuell greifen. Beim ersten Lauf prüfen, ob die Mails im Spam landen.

---

## 6. Betrieb

**Collector:** GitHub Actions Cron, 3×/Tag. Schritte: checkout → deps → `collect.py`
→ neue Parquet-Dateien + `alert_state.json` committen → Alarme sind da bereits raus.
Secrets: `SERPAPI_KEY`, `SMTP_USER`, `SMTP_PASS`.

**Dashboard:** Streamlit Community Cloud, liest dasselbe Repo.

Kosten 0 €, kein Server, unabhängig davon ob der Mac läuft.

**Caveats:**
- Free-Tier-Crons in Actions können sich um Minuten verzögern — bei Tagespreisen irrelevant.
- Community Cloud erlaubt nur *ein* privates App; sonst muss das Repo public sein.
  Dann dürfen **keine** Secrets im Repo liegen (sind sie auch nicht — alles in GH Secrets).
- Actions deaktiviert Cron-Workflows in inaktiven Repos nach 60 Tagen. Da der Workflow
  selbst committet, gilt das Repo als aktiv — trotzdem gelegentlich prüfen.

---

## 7. Meilensteine

| | Inhalt | Aufwand |
|---|---|---|
| **M0** | Repo, venv, SerpApi-Key, `config.yaml`. Smoke-Test: 1 Suche BER→TYO, Datenform prüfen und verifizieren, dass `arrival_id=TYO` als Metro-Code funktioniert | 0,5 T |
| **M1** | `models` / `providers/serpapi` / `store` / `collect` — Gitter läuft, Parquet wächst | 1 T |
| **M2** | Streamlit: KPIs, Preisverlauf, Heatmap, Angebotstabelle | 1 T |
| **M3** | Alarme: Regel-Engine, SMTP, Dedup, Digest | 0,5 T |
| **M4** | GitHub-Actions-Workflow, Streamlit-Cloud-Deploy | 0,5 T |
| **M5** | Härtung: Retries, Provider-Fallback, Quota-Guard, Tests | 0,5 T |

**Reihenfolge-Begründung:** Nach M1 fließen bereits Daten, der Preisverlauf beginnt also,
während M2 gebaut wird. Jeder Tag ohne Collector ist ein Datenpunkt, der nicht nachholbar ist
(nur grob über Travelpayouts rekonstruierbar).

---

## 8. Risiken

| Risiko | Auswirkung | Gegenmaßnahme |
|---|---|---|
| SerpApi-Format ändert sich / fast_flights bricht | keine neuen Daten | Provider-Abstraktion, `snapshots.status` macht Ausfälle im Dashboard sichtbar |
| Quota erschöpft | stille Lücken oder Kosten | harter Monatszähler, Abbruch statt Weiterlauf |
| Alarme landen im Spam | Alarm verpasst | Erstlauf prüfen, Tages-Digest als Lebenszeichen |
| Preis ≠ Endpreis | Erwartung falsch | Gepäck/Sitzplatz nicht enthalten; im Dashboard ausweisen |
| Verfügbarkeit statt Preis ändert sich | scheinbarer Preissprung | Angebotsanzahl je Snapshot mitloggen |
