"""
SPX Force Majeure Watch — Streamlit app.

    Local:   python -m streamlit run streamlit_app.py
    Deploy:  push to GitHub → connect at share.streamlit.io
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
    page_icon="🌋",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .block-container { padding-top: 0.5rem; padding-bottom: 0; }
    footer { display: none; }
    iframe[title="streamlit_app.static_map"] {
        width: 100%; min-height: 88vh; border: none;
    }
</style>
""", unsafe_allow_html=True)

# All files are in the same directory (flat layout)
HERE = Path(__file__).resolve().parent
OUT = HERE  # output goes to the same folder


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
        return {"ok": False, "stdout": "", "stderr": f"ingest.py not found at {script}", "code": 1}

    result = subprocess.run(
        [sys.executable, str(script), "--config", str(HERE / "config.yaml")],
        capture_output=True, text=True, timeout=120,
    )
    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "code": result.returncode,
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
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


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
    if p.exists():
        return p.read_bytes()
    return None


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
    st.title("🌋 Force Majeure Watch")

    if st.button("🔄 Refresh now", use_container_width=True, type="primary"):
        st.cache_data.clear()
        st.rerun()

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

            try:
                dt = datetime.fromisoformat(gen.replace("Z", "+00:00"))
                mins = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
                age = f"{mins} min ago" if mins < 60 else f"{mins // 60}h ago"
            except (ValueError, AttributeError):
                age = "just now"

            st.success(f"**{v}** verified events · updated {age}")
            if n > v:
                st.caption(f"+ {n - v} unverified news lead(s)")

            with st.expander("Feed status"):
                for h in events.get("source_health", []):
                    icon = "✅" if h["state"] == "ok" else \
                           "⚠️" if h["state"] == "disabled" else "❌"
                    detail = f" ({h['events']})" if h["state"] == "ok" else ""
                    if h.get("error"):
                        detail += f" — {h['error'][:80]}"
                    st.text(f"{icon} {h['source']}{detail}")

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
        "Could not build the map. Make sure `map_template.html` "
        "and `latlong.csv` exist in the same folder."
    )
