# SPX force-majeure watch

Pulls active hazard events in Indonesia from official feeds, works out which
SPX facilities each one reaches, and feeds the station map. Two artifacts come
out of every run:

| File | What it is | Who reads it |
|---|---|---|
| `out/events.json` | ~20 KB. Event geometry, severity bands, counts, airport exposure. | The map (polls it every 5 min) |
| `out/impacted_facilities.csv` | One row per affected facility per event, with distance, bearing, severity band and a proposed SLA extension. | You, or whatever job applies the SLA change |

```
feeds ─┐
       ├─▶ normalize ─▶ dedupe ─▶ impact model ─▶ air linehaul ─▶ retention ─┬▶ events.json
overrides.yaml ─┘                      │              │                      └▶ impacted_facilities.csv
                                       │              └── airports.csv + lanes.csv
                                       └── latlong.csv (8,324 facilities)
```

## Quick start

```bash
pip install pyyaml                 # optional but recommended
python ingest.py --dry-run         # fetch, print, write nothing
python ingest.py                   # real run
python build_map.py                # build out/index.html
cd ../out && python -m http.server 8080
```

Open `http://localhost:8080`. Pick an event in the sidebar to see its zone and
the affected facilities. Adjust the radius, filter by station type, export CSV.

Serving it matters — see the next section for why. For a frozen copy you can
email or drop in Slack:

```bash
python build_map.py --embed --out ../out/map.html
```

## How it stays current

This is the part most likely to trip you up, so plainly: **the HTML never
changes. The data file next to it does.**

```
cron, every 10 min          static web host              browser tab
  ingest.py ──writes──▶  events.json  ◀──fetch every 5 min──  index.html
                         index.html   ◀──loaded once────────
```

`index.html` is static and dumb. On load it fetches `events.json`, then
re-fetches it every five minutes with `cache: 'no-store'`. Cron rewrites that
JSON in place. Nobody rebuilds the HTML and nobody reloads the tab.

**It must be served over HTTP.** If you double-click the file, the browser
blocks `fetch` on `file://` for security and the page falls back to whatever
was embedded at build time — then it never updates again. Any static host
works; you do not need an app server:

```bash
cd out && python -m http.server 8080      # fine for one person
```

For a team, drop `index.html` + `events.json` on internal nginx, an S3-style
bucket with static hosting, or whatever serves your dashboards. Cron needs to
run somewhere with outbound access to `data.bmkg.go.id` and `www.gdacs.org`,
and write access to wherever `events.json` is served from. Those can be the
same box or not.

`map.html` is the opposite by design: events embedded, opens with a double
click, **frozen at build time**. That is the one to email or paste in Slack.
Rebuild it to refresh. Do not use it as the ops view.

### Knowing when it has stopped

A polling page fails quietly — it keeps showing yesterday's data with a
politely increasing "updated N min ago" that nobody reads. So the page shouts
instead:

| Condition | What you see |
|---|---|
| `events.json` under 45 min old | Normal status line with the age |
| Over 45 min | Amber banner: *Data is N minutes old* |
| Over 3 hours | Red banner, same message |
| A poll returns non-200 | *Last N refreshes failed*, keeps the last good snapshot but stops implying it is current |
| No `events.json` at all | Tells you to run `ingest.py` or serve the folder |
| A feed failed but the run succeeded | Status line notes it, details under *Feeds and attribution* |

The 45-minute threshold assumes a 10-minute cron, so it means several missed
runs rather than one blip. `STALE_MIN` and `DEAD_MIN` are at the top of the
event-loading section in `map_template.html` if you change the cadence.

Two things the page cannot tell you, so put them in your own monitoring: the
cron not running at all on a host nobody is watching, and `ingest.py` exiting 2
because every feed failed. Both show up as a stale banner **only if someone has
the tab open**. Alert on the exit code and on the mtime of `events.json`.

## Scheduling

Earthquakes are the only thing here that needs minutes. Volcano levels and
flood footprints move on the order of hours.

```cron
*/10 * * * *  cd /srv/spx-fm/ingest && /usr/bin/python3 ingest.py >> ../out/ingest.log 2>&1
```

Airflow, if you already have it:

```python
BashOperator(
    task_id="fm_ingest",
    bash_command="cd /srv/spx-fm/ingest && python ingest.py",
    retries=2, retry_delay=timedelta(minutes=3),
)
```

The script is idempotent and holds no long-lived state beyond
`out/state.json`, which only records when each event was first seen so
`first_seen` survives restarts. Deleting it is harmless.

Failure behaviour worth knowing before you wire alerting to this:

- One source failing never fails the run. You get degraded output and a
  `source_health` entry the map displays.
- If **every** source fails, the script exits 2 and does **not** touch
  `events.json`. A stale map beats an empty one during an incident.
- Writes are atomic (`os.replace`), so the map never reads a half-written file.

## Sources

| Source | Hazards | Status | Notes |
|---|---|---|---|
| **BMKG** open data | earthquake | **Verified live 2026-09-07** | `data.bmkg.go.id/DataMKG/TEWS/{autogempa,gempadirasakan,gempaterkini}.json`. Also gives observed felt-intensity (MMI) per city, which is better than any model. |
| **GDACS** (EC-JRC) | earthquake, flood, cyclone, volcano, wildfire | **Verified live 2026-09-07** | `geteventlist/SEARCH` + `getgeometry`. The geometry endpoint returns real affected-area polygons. |
| **PVMBG / MAGMA** | volcano alert levels | **HTML scrape — will break** | No public JSON API exists. Marked `optional`, so a failure is logged and skipped. |
| **GDELT 2.0** | news leads | **Documented, not verified from here** | Free, no key. Smoke-test before trusting. Everything from it is quarantined (below). |
| X / Twitter | — | **Off by default** | See below. |
| `overrides.yaml` | anything | Manual | Confirmed facts and airport closures. A human beats a guess. |
| **OurAirports** | airport locations | **Public domain** | 69 Indonesian scheduled airports, filtered from `ourairports.com`. CGK cross-checked against Wikipedia. |

Two attribution obligations, both already surfaced in the map's "Feeds and
attribution" panel — keep them there:

- **BMKG requires visible credit** wherever its earthquake data is displayed.
- **GDACS asks to be acknowledged** as the source.

### Why `EVENTS4APP` is not used

GDACS has a convenient `geteventlist/EVENTS4APP` endpoint, but it caps at 100
global events over 4 days. On a normal day, green-level wildfires in Africa and
Australia fill most of it and Indonesian events fall off the end. `SEARCH` with
`country=IDN` and a date window is the correct call.

Note that GDACS tags an event with Indonesia if Indonesia is an *affected*
country, so a centroid can land outside the archipelago. Those are kept and
flagged rather than dropped, because a cyclone centred in the Arafura Sea can
still hit Merauke.

### Why news is quarantined

Official feeds publish a hazard with coordinates and a severity. News publishes
a sentence. You cannot geocode "banjir di Bekasi" to a polygon without guessing,
and a guess here means extending SLA on the wrong region — or worse, failing to
extend it on the right one and having the numbers look fine.

So every news item lands as `status: "unverified"`, with no coordinates and no
impact zone. The map lists it, greys it out, and refuses to count it. It is a
tip-off that something is happening, and a prompt for someone to look.

To promote one, add a real event to a local overrides file with coordinates and
a hazard type, and it flows through the same impact model as everything else.
That deliberate manual step is the feature.

News is still worth having: strikes, demos blocking a toll road, port
congestion and bridge collapses are genuine force-majeure causes that BMKG and
GDACS will never publish.

### On scraping X

You asked about X specifically, so, plainly:

- Scraping x.com breaks their terms of service, and the unofficial endpoints
  rotate often enough that a scraper is a permanent maintenance job.
- The paid API v2 works. Basic tier is around USD 200/month for
  recent-search volume that would cover a few accounts.
- The accounts actually worth reading — `@infoBMKG`, `@id_magma`,
  `@BNPB_Indonesia` — post the same facts that arrive through the official
  feeds above, usually at the same time or later, because the feed is what the
  tweet is generated from.

The config has an `x_twitter` block, disabled, with the account list, so if you
get a key it is a small addition. I would not spend the budget until the free
sources are exhausted.

## Impact models

Two models, and the difference matters when you defend a number.

**polygon** — the hazard footprint is known, from GDACS `getgeometry`. Facility
in or out is a fact. Severity within the footprint still tracks distance from
the source, so bands still apply; anything past the last band is clamped into
it rather than dropped, since the polygon already said it is affected.

**radial** — distance bands from the centre, optionally limited to bearing
corridors. An approximation. Used when no footprint is published.

Corridors exist because wind-driven hazards are not circular. Ash and smoke go
where the wind goes, and a ring overstates one side while understating the
other. Set them per event in `config.yaml` or via an override.

### Earthquake radii are rules of thumb, not physics

`config.yaml` maps magnitude to three radii. These are **not** derived from a
ground-motion model, and the file says so. Writing an attenuation relation
with invented coefficients would give you false precision on the exact number
you would put in front of a regional manager.

Two things make up for it:

1. **BMKG's observed felt-intensity beats the model.** Where BMKG publishes a
   `Dirasakan` string, it is parsed into place-and-MMI pairs and attached to the
   event. The map shows it and says outright that observed readings beat the
   bands. Use it.
2. **The radii are yours to tune.** Recalibrate `magnitude_radius_km` against
   your own incident history — you have the L&D and SLA-breach data to do it
   properly, which is a better calibration set than any published IPE.

Deep quakes get a `deep_radius_multiplier`: they are felt over a wider area but
far less hard at any given point, so a raw magnitude-radius table overstates
them badly. Default cutoff 300 km, multiplier 0.55.

### SLA days are placeholders

`sla_days` in the config and the `+ Days` column in the map default to 3/2/1.
That is a shape, not a recommendation. Set your own policy; the map's numbers
are editable before export and flow into the CSV.

## Air linehaul

The impact a distance model cannot see. When an event shuts an airport, every
facility that flies through it is exposed, however far away.

An event reaches an airport two ways:

- **Declared** — an `airport_closure` event in `overrides.yaml` names IATA
  codes. Use this when the Ministry of Transport announces closures.
- **Inferred** — an airport falls inside an event's own impact zone. Detected
  automatically, no manual step. On the test fixtures the M5.6 near Garut
  swallows BDO at 17 km, KJT at 77, HLP at 117 and CGK at 146.

Facilities are then split two ways:

| | Meaning |
|---|---|
| **Gateway airport closed** | The facility's own nearest airport is shut. |
| **Routes via a closed hub** | Its gateway is open, but the hub it feeds into is shut. |

### The proxy, stated plainly

Gateway = nearest scheduled airport. Via-hub = nearest hub-tier airport to that
gateway. **This is a proxy, and it is the most load-bearing assumption in the
whole pipeline.** Real air freight does not always route through the nearest
airport.

Supply `lanes.csv` and it overrides the proxy entirely:

```csv
station_name,gateway_iata,hub_iata
Rajabasa 2 Hub,TKG,CGK
Makassar 3 Hub,UPG,UPG
```

You do not need a complete file — anything present overrides, anything absent
falls back to nearest-airport. **This is the single highest-value file you can
add here.** Everything else in this pipeline is public data; only you have this.

`gateway_max_km` (default 400) treats a facility further than that from any
airport as road-fed. All 8,324 currently fall inside 400 km of an airport, so
the setting does nothing today — it matters if the network extends into the
interior.

### Air counts are never summed into the severity bands

Ashfall at a hub 40 km away and SLA risk at a facility 1,500 km away that
routes through it are different problems. Adding them produces a number nobody
can defend, so the map reports them as separate rows and the CSV tags them
`Air linehaul (gateway: ...)` or `(via hub: ...)`.

A facility already inside the physical zone is **not** also counted as an air
case — it is that event's problem already, and double-counting would corrupt
any pivot on the CSV. Watch what this does when you drag the radius: on the
Garut quake, going from 170 km to 50 km drops in-zone from 2,116 to 273 while
gateway-only exposure rises from 102 to 1,823. Total exposure barely moves,
because tightening a radius does not close fewer airports. That behaviour is
the point of the whole section.

## Manual overrides

`overrides.yaml` is where confirmed facts go. Two jobs: promote a news lead
once a human has found coordinates, and record what no feed publishes —
airport closures, road cuts, strikes, port backlogs.

Overrides are loaded last so a confirmed manual event survives dedupe against
the automated guess it replaces. Entries expire on their own via `until`, and
an expired entry is logged and skipped rather than silently kept. Templates for
each hazard type are in the file, commented out.

## Tuning

Everything lives in `config.yaml`. The changes you are most likely to make:

- `min_magnitude` — 4.5 is chatty. Raise it if the list is noisy.
- `retention_hours` — how long an event stays visible per hazard type.
- `drop_events_with_zero_facilities` — on by default. An M5 in the Banda Sea
  400 km from anything is not an ops event.
- `impact_models.*.bands` — the radii and labels that drive everything.
- `magnitude_radius_km` — see above.
- `air_linehaul.gateway_max_km` and `air_linehaul.sla_days`.
- `airports.csv` `tier` column — which airports count as hubs for routing. It
  is currently OurAirports' `large_airport` flag, which is a proxy for cargo
  importance. If SPX treats a different set as air hubs, edit the column; the
  model reads it and does not infer it.

`volcanoes.csv` maps PVMBG volcano names to coordinates, because MAGMA
publishes neither. It covers roughly 30 of Indonesia's 127 active volcanoes —
the ones usually above Level II. Any name the pipeline cannot resolve is logged
as `volcano not in gazetteer`; add it when you see it, and verify coordinates
against the Smithsonian Global Volcanism Program first.

## Testing without network

```bash
python ingest.py --save-fixtures fixtures      # once, with network
python ingest.py --fixtures fixtures           # offline, repeatable
```

The bundled `fixtures/` has the real BMKG payload from 2026-09-07 plus GDACS
events covering every code path: polygon and radial, a deep quake, a
centroid-outside-Indonesia cyclone, a missing geometry response, and
duplicate news headlines.

## Known limitations

**Airport closures still need a human.** Nothing publishes Indonesian airport
closures as a feed. NOTAMs exist but are not freely queryable, and the ash
advisories from VAAC Darwin tell you where the ash is, not which runway is
shut. So `airport_closure` events come from `overrides.yaml`. The inferred path
(airport inside an impact zone) is automatic and catches most cases early, but
it flags *risk*, not a confirmed closure.

**Hub tiering is a guess.** 19 of the 69 airports are marked `hub` from
OurAirports' size classification. That is a proxy for cargo importance, not
your network. Fix the `tier` column.

**No administrative boundaries.** Impact is geographic. If you need
"all facilities in Kabupaten Pandeglang" rather than "within 80 km", that needs
a kecamatan/kabupaten shapefile and a spatial join. Worth doing: BNPB and local
BPBD declare impact by administrative area, not by radius, so this is the
mismatch you will feel most when justifying an SLA change.

**MAGMA will break.** It is a scrape. When it does, volcano events still arrive
via GDACS, just without Indonesian alert levels and official exclusion radii.

**GDELT is unverified from my side.** The endpoint is stable and well known, but
I could not reach it to confirm. Run `--dry-run` and check the `gdelt` line in
`source_health` before relying on it.

**Facility list is static.** `latlong.csv` is a snapshot. Wire it to whatever
system owns station master data, or the map will quietly go stale as the
network changes.
