"""
SPX Force Majeure Watch (BCP) — Streamlit app.

Team login routes each team to the map built for them:
  General Map — L&D, HSE, OTM, 3PLM, Control Tower, SLA
  SOC Map     — SOC only (manpower and capacity layers, in development)

NOTE ON SECURITY. This login stops the wrong team opening the wrong map.
It is NOT a confidentiality control: the app runs on public infrastructure
and anything committed to the repo is readable without passing this screen.
"""

import json
import hashlib
from html import escape
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from build_map import load_stations
from stuck_tracker import REFRESH_SECONDS, SnapshotStore, embed_snapshot, refresh_due

st.set_page_config(
    page_title="SPX BCP — Geospatial Analysis",
    page_icon="https://vectorseek.com/wp-content/uploads/2023/11/SPX-Express-Indonesia-white-Logo-Vector.svg-.png",
    layout="wide",
    initial_sidebar_state="collapsed",
)

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# teams and login
# ---------------------------------------------------------------------------
# Each team has its own password so access can be revoked per team.
# Passwords live in Streamlit Cloud -> Settings -> Secrets, never in this file.

TEAMS = {
    "General": {"map": "general"},
    "SOC":     {"map": "soc", "label": "Sorting Center"},
}


def check_password(team: str, entered: str) -> bool:
    try:
        expected = st.secrets["passwords"][team]
    except (KeyError, FileNotFoundError):
        return False
    return bool(expected) and entered == expected


def login_screen():
    st.markdown("""
    <style>
        .block-container { padding-top: 4rem !important; max-width: 460px !important; }
        footer, header { visibility: hidden; }
        .stAppDeployButton { display: none; }
        .login-title { font-size: 26px; font-weight: 700; margin-bottom: 2px; }
        .login-sub { color: #8aa; font-size: 14px; margin-bottom: 26px; }
        .login-note { color: #667; font-size: 12px; margin-top: 22px; line-height: 1.5; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="login-title">SPX - Geospatial Analysis</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="login-sub">Sign in with your team credentials.</div>',
                unsafe_allow_html=True)

    team = st.selectbox("Team", list(TEAMS.keys()),
                        format_func=lambda t: f"{t} Map")
    pwd = st.text_input("Password", type="password")

    if st.button("Sign in", use_container_width=True, type="primary"):
        if check_password(team, pwd):
            st.session_state.team = team
            st.session_state.map_view = TEAMS[team]["map"]
            st.rerun()
        else:
            st.error("Wrong password for that team.")

    st.markdown(
        '<div class="login-note">Contact L&D if you need access.</div>',
        unsafe_allow_html=True)


if "team" not in st.session_state:
    login_screen()
    st.stop()

TEAM = st.session_state.team
VIEW = st.session_state.map_view
VIEW_NAME = "General Map" if VIEW == "general" else "SOC Map"

# ---------------------------------------------------------------------------
# styling
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    .block-container, .stMainBlockContainer {
        padding: 0 !important; margin: 0 !important; max-width: 100% !important;
    }
    footer, header, #MainMenu, .stAppDeployButton { display: none !important; }
    [data-testid="stSidebar"] { display: none !important; }
    .stMain { overflow: hidden; background: #0c1723; }
    .stElementContainer, [data-testid="element-container"] {
        padding: 0 !important; margin: 0 !important;
    }
    [data-testid="stVerticalBlock"] { gap: 0 !important; }
    [data-testid="stHorizontalBlock"] {
        min-height: 48px; height: 48px; align-items: center;
        flex-wrap: nowrap !important; padding: 5px 16px;
        background: #101d2b; border-bottom: 1px solid #283a4b;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"] {
        min-width: 0 !important;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stCaptionContainer"] p {
        color: #a5b8c8; font-size: 11px; margin: 0;
    }
    [data-testid="stPopover"] { display: flex; justify-content: flex-end; }
    [data-testid="stPopover"] > button {
        min-height: 30px; padding: 3px 12px; border: 1px solid #344b60;
        background: #172839; color: #dce8f1; border-radius: 6px;
    }
    [data-testid="stPopover"] > button p { font-size: 12px; }
    [data-testid="stPopoverBody"] { background: #122132; color: #dce8f1; }
    [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] { gap: 10px !important; }
    iframe[title="st.iframe"] {
        display: block; width: 100%; height: calc(100dvh - 48px) !important;
        min-height: 0 !important; border: none; margin: 0;
    }
</style>
""", unsafe_allow_html=True)


def ensure_deps():
    try:
        import yaml  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyyaml", "--quiet"])


def run_ingest() -> dict:
    ensure_deps()
    script = HERE / "ingest.py"
    if not script.exists():
        return {"ok": False, "stdout": "", "stderr": "ingest.py not found", "code": 1}
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--config", str(HERE / "config.yaml")],
            capture_output=True, text=True, timeout=180)
        return {"ok": result.returncode == 0, "stdout": result.stdout,
                "stderr": result.stderr, "code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "",
                "stderr": "Ingestion timed out (180s). MAGMA is likely unreachable.", "code": -1}


def build_map_html(view: str) -> str:
    """
    General Map and SOC Map are built from the same template but different
    data. The network chain is only passed for SOC, so the General Map's HTML
    contains no network data at all rather than hiding it client-side.
    """
    script = HERE / "build_map.py"
    if not script.exists():
        return ""

    out_name = "map_soc.html" if view == "soc" else "map.html"
    cmd = [sys.executable, str(script),
           "--template", str(HERE / "map_template.html"),
           "--stations", str(HERE / "latlong.csv"),
           "--airports", str(HERE / "airports.csv"),
           "--events", str(HERE / "events.json"),
           "--embed", "--out", str(HERE / out_name)]

    if view == "soc":
        net = HERE / "network.csv"
        if net.exists():
            cmd += ["--network", str(net)]
        prof = HERE / "soc_profile.csv"
        if prof.exists():
            cmd += ["--profile", str(prof)]

    subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    p = HERE / out_name
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
def cached_ingest(ts: int) -> dict:
    return run_ingest()


@st.cache_data(ttl=600, show_spinner=False)
def cached_map(ts: int, view: str, revision: tuple) -> str:
    return build_map_html(view)


@st.cache_resource
def tracker_store(parser_revision):
    return SnapshotStore()


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def cached_tracker(settings, credential_revision, station_revision, parser_revision, _service_info):
    stations = load_stations(HERE / "latlong.csv")
    key = (settings["spreadsheet_id"], settings["worksheet_gid"], credential_revision, station_revision)
    return tracker_store(parser_revision).refresh(key, settings, _service_info, stations)


def load_tracker():
    try:
        settings = dict(st.secrets["stuck_tracker"])
        if settings.get("service_account_json"):
            service_info = json.loads(settings["service_account_json"])
        else:
            service_info = dict(st.secrets["gcp_service_account"])
        if not isinstance(service_info, dict):
            raise ValueError("Expected service account JSON")
        settings = {"spreadsheet_id": str(settings["spreadsheet_id"]),
                    "worksheet_gid": int(settings["worksheet_gid"])}
    except (KeyError, FileNotFoundError, ValueError, TypeError):
        return {"status": "unconfigured", "rows": {},
                "message": "A read-only Google Sheets connection needs to be configured by the app owner. No tracker values have been loaded."}
    # Cache identity changes on key rotation, without retaining a key in output.
    revision = hashlib.sha256(json.dumps(service_info, sort_keys=True).encode()).hexdigest()
    return cached_tracker(settings, revision,
                          (HERE / "latlong.csv").stat().st_mtime_ns,
                          (HERE / "stuck_tracker.py").stat().st_mtime_ns, service_info)


def time_bucket():
    return int(time.time()) // 600


def source_time(value):
    from zoneinfo import ZoneInfo
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        age = int((datetime.now(timezone.utc) - stamp).total_seconds() / 60)
        relative = f"{age} min ago" if age >= 0 else f"in {-age} min"
        return stamp.astimezone(ZoneInfo("Asia/Jakarta")).strftime("%d %b %H:%M WIB") + f" ({relative})"
    except (ValueError, AttributeError, TypeError):
        return "not recorded"


def visible_event(ev):
    if ev.get("assessment") != "official_warning":
        return True
    from weather_cap import active_warning
    return active_warning(ev)


# Keep account controls outside the map; the map owns all filters and analysis.
ts = time_bucket()
with st.spinner("Updating the map…"):
    report = cached_ingest(ts)
events = load_events()
tracker = load_tracker() if VIEW == "general" else None

identity, account = st.columns([4, 1])
identity.caption(f"{VIEW_NAME} · Indonesia")
with account.popover("Account"):
    st.markdown(f"**{VIEW_NAME}**")
    st.caption(f"Signed in as {TEAM}")
    if events:
        st.caption("Snapshot: " + source_time(events.get("generated_at")))
    if tracker:
        if tracker.get("fetched_at"):
            st.caption("L&D tracker read: " + source_time(tracker["fetched_at"]))
        if tracker["status"] != "ok":
            st.warning(tracker["message"])
    if st.button("Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    csv_data = load_csv()
    if csv_data:
        st.download_button("Download impact CSV", data=csv_data,
                           file_name="spx-impacted-facilities.csv", mime="text/csv",
                           use_container_width=True)
    if not report["ok"]:
        st.warning("The latest refresh could not finish. Check source freshness in Layers.")
        with st.expander("Refresh diagnostics"):
            st.code(report["stderr"] or report["stdout"] or "No output", language="text")
    st.caption("Use Updates to review events, Facilities to search, Navigate for directions, and Layers for overlays and source freshness.")
    if st.button("Sign out", use_container_width=True):
        for key in ("team", "map_view"):
            st.session_state.pop(key, None)
        st.rerun()

map_revision = tuple((name, (HERE / name).stat().st_mtime_ns)
                     for name in ("events.json", "map_template.html", "build_map.py", "turf.min.js", "navigation.js", "facility_insights.js", "stuck_tracker.js", "stuck_tracker.py", "latlong.csv")
                     if (HERE / name).exists())
html = embed_snapshot(cached_map(ts, VIEW, map_revision), tracker, VIEW)
if html:
    st.components.v1.html(html, height=880 if VIEW == "soc" else 920, scrolling=False)
else:
    st.error("Map could not be built.")


# A running session checks for a new ingestion bucket or repository snapshot.
# The iframe also expires CAP polygons every 30 seconds between app reruns.
st.session_state["map_refresh_checkpoint"] = (ts, map_revision)


@st.fragment(run_every="60s")
def watch_snapshot():
    revision = tuple((name, (HERE / name).stat().st_mtime_ns)
                     for name in ("events.json", "map_template.html", "build_map.py", "turf.min.js", "navigation.js", "facility_insights.js", "stuck_tracker.js", "stuck_tracker.py", "latlong.csv")
                     if (HERE / name).exists())
    if (time_bucket(), revision) != st.session_state.get("map_refresh_checkpoint"):
        st.rerun()
    if VIEW == "general" and refresh_due(tracker):
        st.rerun()


watch_snapshot()
