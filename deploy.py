"""
SPX Force Majeure Watch — deploy & run.

    Double-click this file, or:
    python deploy.py

It will:
  1. Install pyyaml if missing
  2. Run the ingestion pipeline (fetch live events)
  3. Build the map
  4. Start a local web server
  5. Open the map in your browser

After the first run, use:
    python deploy.py --refresh      just re-fetch events, no server
    python deploy.py --serve        just start the server (data already exists)
"""

import os
import sys
import subprocess
import webbrowser
import time
import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
INGEST = HERE / "ingest"
OUT = HERE / "out"

RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def log(color, label, msg):
    print(f"  {color}{BOLD}[{label}]{RESET} {msg}")


def install_deps():
    """Install pyyaml if it is not already available."""
    try:
        import yaml  # noqa: F401
        log(GREEN, " OK ", "pyyaml already installed")
    except ImportError:
        log(YELLOW, "INSTALL", "Installing pyyaml...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "pyyaml", "--quiet"
        ])
        log(GREEN, " OK ", "pyyaml installed")


def run_ingest(use_fixtures=False):
    """Run ingest.py to fetch live events."""
    script = INGEST / "ingest.py"
    if not script.exists():
        log(RED, "FAIL", f"ingest.py not found at {script}")
        log(RED, "    ", f"Make sure the 'ingest' folder is next to deploy.py")
        return False

    cmd = [sys.executable, str(script)]
    if use_fixtures:
        fixtures = HERE / "fixtures"
        if fixtures.is_dir():
            cmd += ["--fixtures", str(fixtures)]
            log(CYAN, "MODE", "Using saved fixtures (offline mode)")
        else:
            log(YELLOW, "WARN", "No fixtures/ folder found, fetching live data")

    log(CYAN, " RUN", "Fetching events from BMKG, GDACS, MAGMA, GDELT...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Show the summary lines
    for line in result.stdout.splitlines():
        if any(k in line for k in ("wrote ", "events ", "facilities:", "airports:",
                                    "routing:", "bmkg:", "gdacs:", "magma:", "gdelt:",
                                    "overrides:", "dedupe:")):
            print(f"         {line.strip()}")

    if result.returncode == 0:
        log(GREEN, " OK ", "Ingestion complete")
        return True
    else:
        log(RED, "FAIL", f"Ingestion failed (exit {result.returncode})")
        for line in (result.stderr or result.stdout or "").splitlines()[-5:]:
            print(f"         {line}")
        return False


def build_map():
    """Build both index.html (live) and map.html (snapshot)."""
    script = INGEST / "build_map.py"
    if not script.exists():
        log(RED, "FAIL", f"build_map.py not found at {script}")
        return False

    OUT.mkdir(parents=True, exist_ok=True)
    ok = True

    # Live version (fetches events.json at runtime)
    r = subprocess.run([
        sys.executable, str(script),
        "--out", str(OUT / "index.html")
    ], capture_output=True, text=True)
    if r.returncode == 0:
        log(GREEN, " OK ", f"Built {OUT / 'index.html'}")
        for line in r.stdout.splitlines():
            if "wrote" in line.lower():
                print(f"         {line.strip()}")
    else:
        log(RED, "FAIL", "Failed to build index.html")
        ok = False

    # Snapshot version (events embedded, works offline)
    r = subprocess.run([
        sys.executable, str(script),
        "--embed", "--out", str(OUT / "map.html")
    ], capture_output=True, text=True)
    if r.returncode == 0:
        log(GREEN, " OK ", f"Built {OUT / 'map.html'} (snapshot, shareable)")
    else:
        log(YELLOW, "WARN", "Failed to build map.html snapshot")

    return ok


def start_server(port=8080):
    """Start a simple HTTP server and open the browser."""
    os.chdir(str(OUT))

    # Check if port is already in use
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            log(YELLOW, "NOTE", f"Port {port} already in use — server may already be running")
            log(CYAN, "OPEN", f"http://localhost:{port}")
            webbrowser.open(f"http://localhost:{port}")
            return

    url = f"http://localhost:{port}"
    log(GREEN, "LIVE", f"Starting server at {BOLD}{url}{RESET}")
    log(CYAN, "    ", "Share this URL with your team (they must be on the same network)")
    log(CYAN, "    ", f"Or share {BOLD}{OUT / 'map.html'}{RESET} as a frozen snapshot")
    log(YELLOW, "    ", "Press Ctrl+C to stop the server\n")

    # Open in browser after a short delay
    import threading
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    # Start serving
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Only log errors, not every GET
            if args and isinstance(args[0], str) and args[0].startswith("GET"):
                return
            super().log_message(fmt, *args)

    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), QuietHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n  {YELLOW}Server stopped.{RESET}")
        server.shutdown()


def main():
    print(f"\n  {BOLD}{'=' * 50}")
    print(f"  SPX Force Majeure Watch — deploy")
    print(f"  {'=' * 50}{RESET}\n")

    ap = argparse.ArgumentParser(description="Deploy the SPX force-majeure map")
    ap.add_argument("--refresh", action="store_true",
                    help="Re-fetch events and rebuild, no server")
    ap.add_argument("--serve", action="store_true",
                    help="Start server only (data must already exist)")
    ap.add_argument("--offline", action="store_true",
                    help="Use saved fixtures instead of live feeds")
    ap.add_argument("--port", type=int, default=8080,
                    help="Port for the local server (default: 8080)")
    args = ap.parse_args()

    if args.serve:
        if not (OUT / "index.html").exists():
            log(RED, "FAIL", "No index.html yet. Run without --serve first.")
            sys.exit(1)
        start_server(args.port)
        return

    # Step 1: dependencies
    install_deps()

    # Step 2: ingest
    ok = run_ingest(use_fixtures=args.offline)
    if not ok:
        log(YELLOW, "WARN", "Ingestion had problems — map may show partial data")

    # Step 3: build
    if not build_map():
        log(RED, "FAIL", "Could not build the map. Check the errors above.")
        sys.exit(1)

    print()
    events = OUT / "events.json"
    if events.exists():
        import json
        d = json.loads(events.read_text(encoding="utf-8"))
        n = d.get("event_count", 0)
        v = d.get("verified_event_count", 0)
        log(GREEN, "DONE", f"{v} verified events, {n - v} unverified leads")

    if args.refresh:
        log(CYAN, "    ", "Data refreshed. Start the server with: python deploy.py --serve")
        return

    # Step 4: serve
    print()
    start_server(args.port)


if __name__ == "__main__":
    main()
