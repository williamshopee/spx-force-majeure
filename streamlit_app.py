"""
SPX Force Majeure Watch — Streamlit app.
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="SPX Force Majeure Watch",
    page_icon="https://upload.wikimedia.org/wikipedia/commons/thumb/0/0e/Shopee_logo.svg/120px-Shopee_logo.svg.png",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .block-container { padding-top: 0.5rem; padding-bottom: 0; }
    footer, header { visibility: hidden; }
    #MainMenu { visibility: hidden; }
    iframe { width: 100%; min-height: 90vh; border: none; }
    [data-testid="stSidebar"] { background: #0f1b2a; }
    [data-testid="stSidebar"] .stMarkdown p,
    [data-testid="stSidebar"] .stMarkdown li,
    [data-testid="stSidebar"] .stCaption { color: #b0c4d8; }
    [data-testid="stSidebar"] h3 { color: #e8f0f8 !important; font-size: 17px; }
    .ev-item { padding: 6px 0; border-bottom: 1px solid #1c3045; }
    .ev-name { font-weight: 600; font-size: 13.5px; color: #dce8f2; }
    .ev-meta { color: #7b96ac; font-size: 12px; }
    .ev-count { float: right; font-family: monospace; color: #9bb; font-size: 13px; }
    .ev-unverified .ev-name { color: #8a8a8a; }
    .feed-ok { color: #4caf80; }
    .feed-fail { color: #e05555; }
    .feed-off { color: #666; }
    .ev-empty { color: #667; font-size: 13px; padding: 10px 0; }
    .filter-head { color: #7b96ac; font-size: 11.5px; text-transform: uppercase;
                   letter-spacing: 0.04em; margin: 10px 0 4px; }
</style>
""", unsafe_allow_html=True)

HERE = Path(__file__).resolve().parent

HAZARD_COLORS = {
    "earthquake": "#e05555",
    "volcano": "#ff5b2b",
    "flood": "#3d9bff",
    "cyclone": "#8f6bff",
    "wildfire": "#ff9a1f",
    "weather": "#4ccfc4",
    "airport_closure": "#5b8fd6",
    "unrest": "#ff6fae",
    "other": "#8fa6b8",
}

ALERT_COLORS = {"red": "#e05555", "orange": "#e08a30", "green": "#4caf80"}


def ensure_deps():
    try:
        import yaml  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "pyyaml", "--quiet"])


def run_ingest() -> dict:
    ensure_deps()
    script = HERE / "ingest.py"
    if not script.exists():
        return {"ok": False, "stdout": "", "stderr": "ingest.py not found", "code": 1}
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--config", str(HERE / "config.yaml")],
            capture_output=True, text=True, timeout=180,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "stdout": "",
            "stderr": "Ingestion timed out (180s). MAGMA is likely unreachable.",
            "code": -1,
        }


def build_map_html() -> str:
    script = HERE / "build_map.py"
    if not script.exists():
        return ""
    subprocess.run(
        [sys.executable, str(script),
         "--template", str(HERE / "map_template.html"),
         "--stations", str(HERE / "latlong.csv"),
         "--airports", str(HERE / "airports.csv"),
         "--events", str(HERE / "events.json"),
         "--embed", "--out", str(HERE / "map.html")],
        capture_output=True, text=True, timeout=60,
    )
    p = HERE / "map.html"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load_events() -> dict | None:
    p = HERE / "events.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_csv() -> bytes | None:
    p = HERE / "impacted_facilities.csv"
    return p.read_bytes() if p.exists() else None


@st.cache_data(ttl=600, show_spinner=False)
def cached_ingest(_ts: int) -> dict:
    return run_ingest()


@st.cache_data(ttl=600, show_spinner=False)
def cached_map(_ts: int) -> str:
    return build_map_html()


def time_bucket():
    return int(time.time()) // 600


def classify_event(ev: dict) -> str:
    """
    Assign each event to a filter category. Volcanoes split by alert level,
    everything else groups by hazard type.
    """
    hazard = ev.get("hazard", "other")
    if hazard == "volcano":
        sev = (ev.get("severity") or ev.get("alert") or "").lower()
        if "awas" in sev or "level iv" in sev:
            return "volcano_iv"
        elif "siaga" in sev or "level iii" in sev:
            return "volcano_iii"
        elif "waspada" in sev or "level ii" in sev:
            return "volcano_ii"
        elif "red" in str(ev.get("alert", "")):
            return "volcano_iv"
        elif "orange" in str(ev.get("alert", "")):
            return "volcano_iii"
        else:
            return "volcano_ii"
    return hazard


FILTER_CONFIG = [
    ("gempa_bumi",       "Gempa Bumi",                        "earthquake",       "#e05555"),
    ("volcano_iv",       "Gunung Api — Level IV (Awas)",       "volcano_iv",       "#ff2020"),
    ("volcano_iii",      "Gunung Api — Level III (Siaga)",     "volcano_iii",      "#ff5b2b"),
    ("volcano_ii",       "Gunung Api — Level II (Waspada)",    "volcano_ii",       "#ff9a1f"),
    ("banjir",           "Banjir",                             "flood",            "#3d9bff"),
    ("siklon",           "Siklon Tropis",                      "cyclone",          "#8f6bff"),
    ("kebakaran",        "Kebakaran Hutan",                    "wildfire",         "#ff9a1f"),
    ("airport",          "Airport Closure",                    "airport_closure",  "#5b8fd6"),
    ("cuaca",            "Cuaca Ekstrem",                      "weather",          "#4ccfc4"),
    ("kerusuhan",        "Kerusuhan / Unrest",                 "unrest",           "#ff6fae"),
    ("lainnya",          "Lainnya",                            "other",            "#8fa6b8"),
]

# Map from classify_event output to filter key
CATEGORY_TO_FILTER = {}
for fkey, _, cat, _ in FILTER_CONFIG:
    CATEGORY_TO_FILTER[cat] = fkey
# earthquake is stored as "earthquake" by classify_event but filter key is "gempa_bumi"
CATEGORY_TO_FILTER["earthquake"] = "gempa_bumi"
CATEGORY_TO_FILTER["flood"] = "banjir"
CATEGORY_TO_FILTER["cyclone"] = "siklon"
CATEGORY_TO_FILTER["wildfire"] = "kebakaran"
CATEGORY_TO_FILTER["airport_closure"] = "airport"
CATEGORY_TO_FILTER["weather"] = "cuaca"
CATEGORY_TO_FILTER["unrest"] = "kerusuhan"
CATEGORY_TO_FILTER["other"] = "lainnya"


# ---------------------------------------------------------------------------
# sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### SPX L&D — Force Majeure Watch")

    if st.button("Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    ts = time_bucket()
    with st.spinner("Fetching from BMKG, GDACS, MAGMA..."):
        report = cached_ingest(ts)

    if not report["ok"]:
        st.warning(f"Ingestion issue: {report['stderr'][:120]}")
        with st.expander("Details"):
            st.code(report["stderr"] or report["stdout"] or "No output",
                    language="text")

    events = load_events()
    if events:
        gen = events.get("generated_at", "")
        n = events.get("event_count", 0)
        v = events.get("verified_event_count", 0)

        try:
            dt = datetime.fromisoformat(gen.replace("Z", "+00:00"))
            mins = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
            age = f"{mins} min ago" if mins < 60 else f"{mins // 60}h ago"
        except (ValueError, AttributeError):
            age = "just now"

        failed = [h for h in events.get("source_health", [])
                  if h["state"] == "failed"]

        status = f"{v} verified event{'s' if v != 1 else ''}"
        if n > v:
            status += f", {n - v} unverified"
        status += f" — updated {age}"
        if failed:
            status += f". {len(failed)} feed{'s' if len(failed) != 1 else ''} down."

        st.caption(status)

        with st.expander("Feed status"):
            for h in events.get("source_health", []):
                if h["state"] == "ok":
                    cls, icon = "feed-ok", "●"
                elif h["state"] == "disabled":
                    cls, icon = "feed-off", "○"
                else:
                    cls, icon = "feed-fail", "✕"
                detail = f" ({h['events']})" if h["state"] == "ok" else ""
                if h.get("error"):
                    detail += f" — {h['error'][:60]}"
                st.markdown(
                    f"<span class='{cls}'>{icon}</span> {h['source']}{detail}",
                    unsafe_allow_html=True)

        st.markdown("---")

        # --- classify all events ---
        all_events = events.get("events", [])
        for ev in all_events:
            ev["_category"] = classify_event(ev)
            ev["_filter_key"] = CATEGORY_TO_FILTER.get(ev["_category"], "lainnya")

        # count per filter category
        counts = {}
        for ev in all_events:
            k = ev["_filter_key"]
            counts[k] = counts.get(k, 0) + 1

        # only show filters that have events
        active_filters = [f for f in FILTER_CONFIG if f[0] in counts]

        if active_filters:
            st.markdown('<div class="filter-head">Filter by type</div>',
                        unsafe_allow_html=True)

            for fkey, label, cat, color in active_filters:
                c = counts.get(fkey, 0)
                default = True
                st.checkbox(
                    f"{label} ({c})",
                    value=default,
                    key=f"filter_{fkey}",
                )

        # determine which filter keys are checked
        active_keys = set()
        for fkey, label, cat, color in FILTER_CONFIG:
            if st.session_state.get(f"filter_{fkey}", True):
                active_keys.add(fkey)

        filtered = [ev for ev in all_events if ev["_filter_key"] in active_keys]

        st.markdown("---")

        # --- event list ---
        if not filtered:
            st.markdown(
                '<div class="ev-empty">No events match the selected filters.</div>',
                unsafe_allow_html=True)
        else:
            for ev in filtered:
                imp = ev.get("impact") or {}
                air = ev.get("air") or {}
                total = (imp.get("total", 0) + air.get("direct_count", 0)
                         + air.get("via_hub_count", 0))

                hazard = ev.get("hazard", "other")
                color = HAZARD_COLORS.get(hazard, "#666")
                # use the filter color for volcanoes to distinguish levels
                for fkey, _, cat, fcolor in FILTER_CONFIG:
                    if fkey == ev["_filter_key"]:
                        color = fcolor
                        break

                unv = ev.get("status") == "unverified"
                cls = "ev-item ev-unverified" if unv else "ev-item"

                name = ev.get("name", "Unknown")[:58]
                meta_parts = [ev.get("severity", ""), ev.get("source", "")]
                if unv:
                    meta_parts.append("unverified")
                meta = " · ".join(p for p in meta_parts if p)

                st.markdown(
                    f'<div class="{cls}">'
                    f'<span class="ev-count">{total:,}</span>'
                    f'<span style="color:{color}">●</span> '
                    f'<span class="ev-name">{name}</span><br>'
                    f'<span class="ev-meta">{meta}</span>'
                    f'</div>',
                    unsafe_allow_html=True)

        st.markdown("---")

        csv_data = load_csv()
        if csv_data:
            stamp = datetime.now().strftime("%Y-%m-%d")
            st.download_button(
                "Download impacted facilities (.csv)",
                data=csv_data,
                file_name=f"spx-impacted-facilities-{stamp}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with st.expander("Attribution"):
            for a in events.get("attribution", []):
                st.caption(a)
    else:
        st.info("No event data yet. Click Refresh data or wait for the first fetch.")

    st.markdown("---")
    st.caption("Select an event on the map to view the impact zone and export affected facilities.")

# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------

with st.spinner("Loading map..."):
    html = cached_map(ts)

if html:
    st.components.v1.html(html, height=920, scrolling=False)
else:
    st.error("Map could not be built. Verify map_template.html and latlong.csv exist.")
