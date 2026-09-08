"""
SPX Force Majeure Watch — Streamlit app.

    Local:   streamlit run streamlit_app.py
    Deploy:  push to GitHub → connect at share.streamlit.io
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="SPX Force Majeure Watch",
    page_icon="🌋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# kill the default Streamlit padding so the map fills the viewport
st.markdown("""
<style>
    /* remove top padding and footer */
    .block-container { padding-top: 0.5rem; padding-bottom: 0; }
    footer { display: none; }
    /* make the iframe (map) fill the space */
    iframe[title="streamlit_app.static_map"] {
        width: 100%; min-height: 88vh; border: none;
    }
</style>
""", unsafe_allow_html=True)

HERE = Path(__file__).resolve().parent
INGEST = HERE / "ingest"
OUT = HERE / "out"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def ensure_deps():
    """Install pyyaml if missing (needed for config parsing)."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "pyyaml", "--quiet"])


def run_ingest() -> dict:
    """Run the ingestion pipeline and return the health report."""
    ensure_deps()
    OUT.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [sys.executable, str(INGEST / "ingest.py")],
        capture_output=True, text=True, timeout=120,
    )
    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "code": result.returncode,
    }


def build_map_html() -> str:
    """Build the map with events embedded and return the HTML string."""
    subprocess.run(
        [sys.executable, str(INGEST / "build_map.py"),
         "--embed", "--out", str(OUT / "map.html")],
        capture_output=True, text=True, timeout=60,
    )
    p = OUT / "map.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def load_events() -> dict | None:
    p = OUT / "events.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_csv() -> bytes | None:
    p = OUT / "impacted_facilities.csv"
    if p.exists():
        return p.read_bytes()
    return None


# ---------------------------------------------------------------------------
# ingest (cached for 10 minutes so page interactions don't re-fetch)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner=False)
def cached_ingest(_ts: int) -> dict:
    """
    _ts is a 10-minute bucket so the cache expires naturally.
    The underscore prefix tells Streamlit not to hash it.
    """
    return run_ingest()


@st.cache_data(ttl=600, show_spinner=False)
def cached_map(_ts: int) -> str:
    return build_map_html()


def time_bucket():
    """Returns a value that changes every 10 minutes."""
    return int(time.time()) // 600


# ---------------------------------------------------------------------------
# sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🌋 Force Majeure Watch")

    if st.button("🔄 Refresh now", use_container_width=True, type="primary"):
        st.cache_data.clear()
        st.rerun()

    # run ingest
    ts = time_bucket()
    with st.spinner("Fetching events from BMKG, GDACS, MAGMA..."):
        report = cached_ingest(ts)

    if not report["ok"]:
        st.error(f"Ingestion failed (exit {report['code']})")
        with st.expander("Error log"):
            st.code(report["stderr"] or report["stdout"] or "No output",
                    language="text")
    else:
        events = load_events()
        if events:
            gen = events.get("generated_at", "")
            n = events.get("event_count", 0)
            v = events.get("verified_event_count", 0)

            # age
            try:
                dt = datetime.fromisoformat(gen.replace("Z", "+00:00"))
                mins = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
                age = f"{mins} min ago" if mins < 60 else f"{mins // 60}h ago"
            except (ValueError, AttributeError):
                age = "just now"

            st.success(f"**{v}** verified events · updated {age}")
            if n > v:
                st.caption(f"+ {n - v} unverified news lead(s)")

            # source health
            with st.expander("Feed status"):
                for h in events.get("source_health", []):
                    icon = "✅" if h["state"] == "ok" else \
                           "⚠️" if h["state"] == "disabled" else "❌"
                    detail = f" ({h['events']})" if h["state"] == "ok" else ""
                    if h.get("error"):
                        detail += f" — {h['error'][:80]}"
                    st.text(f"{icon} {h['source']}{detail}")

            # event list
            st.divider()
            st.caption("ACTIVE EVENTS")
            for ev in events.get("events", []):
                imp = ev.get("impact") or {}
                air = ev.get("air") or {}
                total = imp.get("total", 0) + air.get("direct_count", 0) + \
                        air.get("via_hub_count", 0)
                status = "🔴" if ev.get("alert") == "red" else \
                         "🟠" if ev.get("alert") == "orange" else \
                         "🟡" if ev.get("alert") == "green" else "⚪"
                label = ev.get("name", "Unknown")[:55]
                if ev.get("status") == "unverified":
                    status = "⚪"
                    label += " *(unverified)*"
                st.markdown(
                    f"{status} **{label}**  \n"
                    f"<small style='color:#888'>{ev.get('severity','')} · "
                    f"{ev.get('source','')} · "
                    f"**{total:,}** facilities</small>",
                    unsafe_allow_html=True,
                )

            # downloads
            st.divider()
            csv_data = load_csv()
            if csv_data:
                stamp = datetime.now().strftime("%Y-%m-%d")
                st.download_button(
                    "📥 Download impacted facilities (CSV)",
                    data=csv_data,
                    file_name=f"spx-impacted-facilities-{stamp}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            # attribution
            with st.expander("Attribution"):
                for a in events.get("attribution", []):
                    st.caption(a)
        else:
            st.warning("No events.json found. Check the ingest log.")

    st.divider()
    st.caption(
        "Pick an event on the map sidebar to see its zone. "
        "Data refreshes every 10 minutes automatically."
    )

# ---------------------------------------------------------------------------
# main: the map
# ---------------------------------------------------------------------------

with st.spinner("Building map..."):
    html = cached_map(ts)

if html:
    st.components.v1.html(html, height=920, scrolling=False)
else:
    st.error(
        "Could not build the map. Make sure `ingest/map_template.html` "
        "and `ingest/latlong.csv` exist."
    )
