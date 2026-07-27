"""Weekly anonymous whale flow report — template or OpenAI."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalelib.options_flow_config import OPENAI_API_KEY_ENV
from whalelib.options_ledger import sector_rollups

CACHE = ROOT / "output" / "cache"
OUT = ROOT / "output"
EVENTS_PATH = CACHE / "options_flow_events.json"
LEDGER_PATH = CACHE / "options_ledger.json"


def _load_env() -> None:
    for p in (ROOT / ".env", ROOT.parent / ".env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in (OPENAI_API_KEY_ENV, "OPENAI_API_KEY"):
                    os.environ[OPENAI_API_KEY_ENV] = v.strip().strip('"')


def _week_events(events: list[dict], *, days: int = 7) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [e for e in events if (e.get("trade_date") or "") >= cutoff]


def _template_report(rollup: dict, ledger: dict, *, days: int) -> str:
    lines = [
        f"# Anonymous whale flow report ({days}d)",
        "",
        f"- Large-flow events: **{rollup.get('event_count', 0)}**",
        f"- Est. put premium: **${rollup.get('put_premium_usd', 0)/1e9:.2f}B**",
        f"- Est. call premium: **${rollup.get('call_premium_usd', 0)/1e9:.2f}B**",
        f"- Put share: **{rollup.get('put_share_pct', 0)}%**",
        "",
        "## Top underlyings (anonymous)",
        "",
    ]
    for x in rollup.get("top_underlyings", [])[:20]:
        lines.append(f"- **{x['ticker']}** — ${x['premium_usd']/1e6:.0f}M est. premium")
    lines.extend(["", "## Inferred open book (top positions)", ""])
    open_pos = [p for p in ledger.get("positions", []) if p.get("status") == "open"]
    open_pos.sort(key=lambda p: float(p.get("est_premium_usd") or 0), reverse=True)
    for p in open_pos[:15]:
        lines.append(
            f"- {p['underlying']} {p['option_type'].upper()} ${p.get('strike')} "
            f"exp {p.get('expiry')} — ~${float(p.get('est_premium_usd',0))/1e6:.1f}M ({p.get('bias')})"
        )
    lines.extend([
        "",
        "## Patterns to watch",
        "",
        "- Cluster of **large puts** across semis/ETFs (SMH, NVDA, AVGO…) = anonymous bearish book building.",
        "- Rising **put share %** week-over-week without single-name attribution.",
        "- Cross-check later with **13F put diffs** on tier-S funds (e.g. SA).",
        "",
        "*Identity is not knowable from public tape; this is flow-only intelligence.*",
    ])
    return "\n".join(lines)


def _llm_report(context: str, *, api_key: str) -> str:
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write a concise weekly 'anonymous whale flow' briefing for traders. "
                    "Data is options large-flow only — no fund names. Highlight sector clusters, "
                    "put/call skew, semis vs infra, and what might matter next week. Under 400 words."
                ),
            },
            {"role": "user", "content": context},
        ],
        "temperature": 0.3,
    }
    req = Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--llm", action="store_true", help="Use OpenAI if API key set")
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--discord", action="store_true")
    args = ap.parse_args()

    _load_env()
    events = json.loads(EVENTS_PATH.read_text(encoding="utf-8")) if EVENTS_PATH.is_file() else []
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8")) if LEDGER_PATH.is_file() else {"positions": []}
    week = _week_events(events, days=args.days)
    rollup = sector_rollups(week)

    body = _template_report(rollup, ledger, days=args.days)
    api_key = (os.environ.get(OPENAI_API_KEY_ENV) or os.environ.get("OPENAI_API_KEY") or "").strip()
    if args.llm and api_key:
        try:
            ctx = json.dumps({"rollup": rollup, "open_positions": ledger.get("positions", [])[:30]}, indent=2)
            body = _llm_report(ctx, api_key=api_key)
        except Exception as exc:
            body += f"\n\n*(LLM failed: {exc!r}; template above.)*"

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "options_weekly_report.md"
    if args.write_report:
        out_path.write_text(body, encoding="utf-8")
        print(f"Wrote {out_path}")

    if args.discord:
        print(body[:1900])

    if not args.write_report and not args.discord:
        print(body[:2000])


if __name__ == "__main__":
    main()
