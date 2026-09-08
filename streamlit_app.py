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
    /* title visible on dark sidebar */
    [data-testid="stSidebar"] h3 { color: #e8f0f8 !important; font-size: 17px; }
    .ev-item { padding: 6px 0; border-bottom: 1px solid #1c3045; }
    .ev-name { font-weight: 600; font-size: 13.5px; color: #dce8f2; }
    .ev-meta { color: #7b96ac; font-size: 12px; }
    .ev-count { float: right; font-family: monospace; color: #9bb; font-size: 13px; }
    .ev-unverified .ev-name { color: #8a8a8a; }
    .feed-ok { color: #4caf80; }
    .feed-fail { color: #e05555; }
    .feed-off { color: #666; }
</style>
""", unsafe_allow_html=True)

HERE = Path(__file__).resolve().parent


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
        # MAGMA is the usual cause — it retries 3x against a flaky server.
        # If events.json already exists from a prior run, the map still works.
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


ALERT_COLORS = {"red": "#e05555", "orange": "#e08a30", "green": "#4caf80"}

with st.sidebar:
    st.markdown("### SPX L&D — Force Majeure Watch")

    if st.button("Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    ts = time_bucket()
    with st.spinner("Fetching from BMKG, GDACS, MAGMA..."):
        report = cached_ingest(ts)

    if not report["ok"]:
        # Show warning but still try to load whatever data exists
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

        for ev in events.get("events", []):
            imp = ev.get("impact") or {}
            air = ev.get("air") or {}
            total = (imp.get("total", 0) + air.get("direct_count", 0)
                     + air.get("via_hub_count", 0))

            alert = ev.get("alert", "")
            color = ALERT_COLORS.get(alert, "#666")
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
