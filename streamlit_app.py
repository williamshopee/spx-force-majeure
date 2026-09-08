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

    /* --- widget text visibility fix ---
       Streamlit renders checkbox/expander/label text with its own default
       (near-black) color, which does not inherit page CSS and is invisible
       against this dark sidebar. Force it everywhere, broadly, since the
       exact internal data-testid nesting varies by Streamlit version. */
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] label *,
    [data-testid="stSidebar"] [data-testid="stCheckbox"],
    [data-testid="stSidebar"] [data-testid="stCheckbox"] *,
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"],
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] *,
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] * {
        color: #dce8f2 !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] summary,
    [data-testid="stSidebar"] [data-testid="stExpander"] summary * {
        color: #dce8f2 !important;
        background: transparent;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] svg {
        fill: #7b96ac;
    }

    .ev-item { padding: 6px 0; border-bottom: 1px solid #1c3045; }
    .ev-name { font-weight: 600; font-size: 13.5px; color: #dce8f2; }
    .ev-meta { color: #7b96ac; font-size: 12px; }
    .ev-count { float: right; font-family: monospace; color: #9bb; font-size: 13px; }
    .ev-unverified .ev-name { color: #8a8a8a; }
    .feed-ok { color: #4caf80; }
    .feed-fail { color: #e05555; }
    .feed-off { color: #666; }
    /* filter pills */
    .hazard-filters { display: flex; flex-wrap: wrap; gap: 5px; margin: 8px 0 12px; }
    .hazard-pill {
        display: inline-block; font-size: 12px; padding: 4px 10px;
        border-radius: 3px; border: 1px solid #2a3f55; color: #8aa; cursor: default;
    }
    .hazard-pill.active { border-color: #5a8ab0; color: #e0ecf5; background: #1a3048; }
    .hazard-pill .dot {
        display: inline-block; width: 7px; height: 7px; border-radius: 50%;
        margin-right: 5px; vertical-align: middle;
    }
    .ev-empty { color: #667; font-size: 13px; padding: 10px 0; }
</style>
""", unsafe_allow_html=True)

HERE = Path(__file__).resolve().parent

HAZARD_COLORS = {
    "earthquake": "#f4d35e",
    "volcano": "#ff5b2b",
    "flood": "#3d9bff",
    "cyclone": "#8f6bff",
    "wildfire": "#ff9a1f",
    "weather": "#4ccfc4",
    "airport_closure": "#5b8fd6",
    "unrest": "#ff6fae",
    "other": "#8fa6b8",
}

HAZARD_LABELS = {
    "earthquake": "Earthquake",
    "volcano": "Volcano",
    "flood": "Flood",
    "cyclone": "Cyclone",
    "wildfire": "Wildfire",
    "weather": "Weather",
    "airport_closure": "Airport closure",
    "unrest": "Unrest",
    "other": "Other",
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
            "ok": False,
            "stdout": "",
            "stderr": "Ingestion timed out (180s). MAGMA is likely unreachable. "
                      "Other feeds may have written partial data.",
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

        # --- hazard type filters ---
        all_events = events.get("events", [])
        present = sorted(set(ev.get("hazard", "other") for ev in all_events))

        if "hazard_filter" not in st.session_state:
            st.session_state.hazard_filter = set(present)

        filter_cols = st.columns(min(len(present), 4))
        for i, h in enumerate(present):
            col = filter_cols[i % len(filter_cols)]
            label = HAZARD_LABELS.get(h, h.title())
            count = sum(1 for ev in all_events if ev.get("hazard") == h)
            active = h in st.session_state.hazard_filter
            if col.checkbox(f"{label} ({count})", value=active, key=f"f_{h}"):
                st.session_state.hazard_filter.add(h)
            else:
                st.session_state.hazard_filter.discard(h)

        filtered = [ev for ev in all_events
                    if ev.get("hazard", "other") in st.session_state.hazard_filter]

        st.markdown("---")

        # --- event list ---
        if not filtered:
            st.markdown('<div class="ev-empty">No events match the selected types.</div>',
                        unsafe_allow_html=True)
        else:
            for ev in filtered:
                imp = ev.get("impact") or {}
                air = ev.get("air") or {}
                total = (imp.get("total", 0) + air.get("direct_count", 0)
                         + air.get("via_hub_count", 0))

                hazard = ev.get("hazard", "other")
                color = HAZARD_COLORS.get(hazard, "#666")
                alert = ev.get("alert", "")
                alert_color = ALERT_COLORS.get(alert, color)
                unv = ev.get("status") == "unverified"
                cls = "ev-item ev-unverified" if unv else "ev-item"

                name = ev.get("name", "Unknown")[:58]
                meta_parts = [
                    HAZARD_LABELS.get(hazard, hazard),
                    ev.get("severity", ""),
                    ev.get("source", ""),
                ]
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
