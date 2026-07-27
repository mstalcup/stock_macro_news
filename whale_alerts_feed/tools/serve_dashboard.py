"""Serve 13F dashboard locally (optional — file:// + dashboard_data.js also works)."""
from __future__ import annotations

import http.server
import socketserver
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "output" / "dashboard"
PORT = 8765


def main() -> None:
    if not DASH.is_dir():
        print(f"Missing {DASH} — run build_dashboard_data.py first")
        raise SystemExit(1)
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(DASH))
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"Serving {DASH}")
        print(f"Open http://127.0.0.1:{PORT}/index.html")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
