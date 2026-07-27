"""8-K stake / ownership disclosures — issuer-side, regex parsing (no LLM)."""
from __future__ import annotations

import re
import time
from datetime import date, timedelta

from .edgar import (
    _archive_base,
    _get_text,
    build_name_ticker_map,
    company_tickers_map,
    filing_doc_urls,
    issuer_cik_from_hit,
    normalize_accession,
    parse_issuer_ticker_from_text,
    parse_schedule_entities,
    search_filings,
    submissions_for_cik,
    ticker_from_display_name,
)
from .types import WhaleSignal

_STAKE_STRONG_RE = re.compile(
    r"(schedule\s+13[dg]|5(?:\.\d+)?\s*%\s+of\s+the\s+(?:outstanding|common|class)|"
    r"ownership\s+of\s+more\s+than\s+5\s*%)",
    re.I,
)
_STAKE_CONTEXT_RE = re.compile(
    r"(beneficial\s+own|activist\s+investor|passive\s+investor|"
    r"acquired.*(?:stake|position|shares)|builds?\s+(?:a\s+)?stake)",
    re.I,
)
_ITEM_RE = re.compile(r"item\s+(\d+\.\d+)", re.I)

# Extra aliases beyond fund_name (substring match in filing text).
FUND_ALIASES: dict[str, list[str]] = {
    "1336528": ["Pershing Square", "PSCM"],
    "1517137": ["Starboard Value", "Starboard Group"],
    "1350694": ["Elliott Associates", "Elliott Management"],
    "1040273": ["Third Point", "Daniel Loeb"],
    "1138995": ["ValueAct Capital", "ValueAct"],
    "1364742": ["Greenlight Capital", "David Einhorn"],
    "1649339": ["Scion Asset", "Michael Burry"],
    "1656456": ["Appaloosa", "David Tepper"],
    "1709323": ["Himalaya Capital", "Li Lu"],
    "2045724": ["Situational Awareness"],
}


def fund_search_patterns(funds: list[dict]) -> list[tuple[str, str, str]]:
    """(cik, fund_name, pattern) sorted longest-first to reduce false positives."""
    patterns: list[tuple[str, str, str]] = []
    for f in funds:
        cik = str(f.get("cik") or "").lstrip("0")
        name = (f.get("fund_name") or "").strip()
        if not cik or not name:
            continue
        variants = [name]
        if "(" in name:
            variants.append(name.split("(")[0].strip())
        for alias in FUND_ALIASES.get(cik, []):
            variants.append(alias)
        seen: set[str] = set()
        for v in variants:
            up = v.upper()
            if len(up) < 6 or up in seen:
                continue
            seen.add(up)
            patterns.append((cik, name, up))
    patterns.sort(key=lambda x: -len(x[2]))
    return patterns


def is_stake_disclosure(text: str) -> bool:
    if _STAKE_STRONG_RE.search(text):
        return True
    return bool(_STAKE_CONTEXT_RE.search(text) and re.search(r"investor|holder|stockholder", text, re.I))


def match_fund_in_text(text: str, funds: list[dict]) -> tuple[str, str] | None:
    plain = re.sub(r"<[^>]+>", " ", text or "")
    plain = re.sub(r"\s+", " ", plain).upper()
    for cik, name, pat in fund_search_patterns(funds):
        if pat in plain:
            return cik, name
    return None


def eight_k_filings_for_cik(cik: str, *, years: int = 2) -> list[dict]:
    subs = submissions_for_cik(cik)
    if not subs:
        return []
    name = (subs.get("name") or "").strip()
    recent = subs.get("filings", {}).get("recent") or {}
    accession = recent.get("accessionNumber") or []
    filing_date = recent.get("filingDate") or []
    form = recent.get("form") or []
    primary = recent.get("primaryDocument") or []
    cutoff = (date.today() - timedelta(days=years * 365)).isoformat()
    out: list[dict] = []
    for i, acc in enumerate(accession):
        if i >= len(form) or i >= len(filing_date):
            break
        fd = (filing_date[i] or "")[:10]
        if fd and fd < cutoff:
            continue
        if (form[i] or "").upper() != "8-K":
            continue
        nodash, dashed = normalize_accession(acc)
        prim = (primary[i] or "").strip() if i < len(primary) else ""
        out.append(
            {
                "accession": nodash,
                "accession_dashed": dashed,
                "file_date": fd,
                "form": form[i],
                "filer_cik": str(cik).lstrip("0"),
                "filer_name": name,
                "primary_document": prim,
            }
        )
    return out


def _filing_text_bundle(cik: str, accession: str, *, primary_document: str = "") -> str:
    parts: list[str] = []
    for url in filing_doc_urls(
        cik,
        accession,
        ("8-k", ".htm", ".txt"),
        primary_document=primary_document,
        limit=4,
    ):
        parts.append(_get_text(url))
    return "\n".join(p for p in parts if p)


def parse_issuer_8k_stake(
    hit: dict,
    funds: list[dict],
    cik_map: dict[str, str],
    name_map: dict[str, str],
) -> dict | None:
    """
    Issuer-filed 8-K: extract ticker + roster fund from filing text.
    Returns enriched hit or None.
    """
    row = parse_schedule_entities(hit)
    issuer_cik = issuer_cik_from_hit(row)
    acc = row.get("accession_dashed") or ""
    if not issuer_cik or not acc:
        return None

    ticker = (row.get("issuer_ticker") or "").upper()
    if not ticker:
        for n in row.get("display_names") or []:
            ticker = ticker_from_display_name(n)
            if ticker:
                break

    text = _filing_text_bundle(issuer_cik, acc)
    if not text or not is_stake_disclosure(text):
        return None

    fund_match = match_fund_in_text(text, funds)
    if not fund_match:
        return None
    filer_cik, filer_name = fund_match

    if not ticker:
        ticker, issuer_name = parse_issuer_ticker_from_text(text, cik_map, name_map)
    else:
        issuer_name = row.get("issuer_name") or ""

    if not ticker:
        return None

    _, base = _archive_base(issuer_cik, acc)
    return {
        **row,
        "issuer_cik": issuer_cik,
        "issuer_ticker": ticker,
        "issuer_name": issuer_name,
        "filer_cik": filer_cik,
        "filer_name": filer_name,
        "signal_type": "8k_stake",
        "items": _ITEM_RE.findall(text[:8000])[:6],
        "source_url": f"{base}/{acc}-index.htm",
    }


def eight_k_to_signals(hits: list[dict]) -> list[WhaleSignal]:
    out: list[WhaleSignal] = []
    for h in hits:
        ticker = (h.get("issuer_ticker") or "").upper()
        if not ticker:
            continue
        acc = h.get("accession_dashed") or h.get("accession") or ""
        out.append(
            WhaleSignal(
                signal_id=f"8k_stake#{acc}#{ticker}#{h.get('filer_cik','')}",
                signal_type="8k_stake",
                signal_date=h.get("file_date") or "",
                filer_cik=str(h.get("filer_cik") or "").lstrip("0"),
                filer_name=h.get("filer_name") or "",
                ticker=ticker,
                issuer_name=h.get("issuer_name") or "",
                accession=acc,
                alert_class="primary",
                meta={"items": h.get("items") or [], "issuer_cik": h.get("issuer_cik") or ""},
            )
        )
    return out


def search_8k_stake_hits(*, start: date, end: date, max_pages: int = 8) -> list[dict]:
    """Issuer 8-Ks mentioning beneficial ownership (EFTS full-text)."""
    queries = ('"beneficial owner"', '"Schedule 13D"')
    seen: set[str] = set()
    hits: list[dict] = []
    for q in queries:
        for attempt in range(2):
            batch = search_filings(
                forms=["8-K"], start_date=start, end_date=end, q=q, max_pages=max_pages
            )
            if batch:
                break
            time.sleep(1.5)
        for h in batch:
            acc = h.get("accession_dashed") or h.get("accession") or ""
            if acc in seen:
                continue
            seen.add(acc)
            hits.append(h)
    return hits


def efts_8k_stake_signals(
    funds: list[dict],
    *,
    years: int = 2,
    parse_limit: int = 200,
) -> list[WhaleSignal]:
    end = date.today()
    start = end - timedelta(days=int(years * 365))
    cik_map = company_tickers_map()
    name_map = build_name_ticker_map()
    raw = search_8k_stake_hits(start=start, end=end)
    enriched: list[dict] = []
    for hit in raw[:parse_limit]:
        parsed = parse_issuer_8k_stake(hit, funds, cik_map, name_map)
        if parsed:
            enriched.append(parsed)
    return eight_k_to_signals(enriched)


def _issuer_cik_for_ticker(ticker: str, cik_map: dict[str, str]) -> str:
    up = (ticker or "").upper()
    for cik, t in cik_map.items():
        if t == up:
            return cik
    return ""


def _issuer_8k_in_window(issuer_cik: str, *, start: date, end: date) -> list[dict]:
    subs = submissions_for_cik(issuer_cik)
    if not subs:
        return []
    recent = subs.get("filings", {}).get("recent") or {}
    accession = recent.get("accessionNumber") or []
    filing_date = recent.get("filingDate") or []
    form = recent.get("form") or []
    primary = recent.get("primaryDocument") or []
    out: list[dict] = []
    for i, acc in enumerate(accession):
        if i >= len(form) or i >= len(filing_date):
            break
        fd = (filing_date[i] or "")[:10]
        if not fd or fd < start.isoformat() or fd > end.isoformat():
            continue
        if (form[i] or "").upper() != "8-K":
            continue
        nodash, dashed = normalize_accession(acc)
        out.append(
            {
                "accession": nodash,
                "accession_dashed": dashed,
                "file_date": fd,
                "form": form[i],
                "filer_cik": issuer_cik,
                "primary_document": (primary[i] or "").strip() if i < len(primary) else "",
                "display_names": [(subs.get("name") or "")],
            }
        )
    return out


def eight_k_signals_from_schedule(
    schedule_rows: list[dict],
    funds: list[dict],
    *,
    lookback_days: int = 45,
) -> list[WhaleSignal]:
    """
    For each 13D/G event, find issuer 8-K in prior window mentioning roster fund.
    Signal date = 8-K date (earlier than schedule filing).
    """
    cik_map = company_tickers_map()
    name_map = build_name_ticker_map()
    patterns = fund_search_patterns(funds)
    out: list[WhaleSignal] = []
    seen: set[str] = set()
    deduped_rows: list[dict] = []
    row_keys: set[tuple[str, str, str]] = set()
    for row in schedule_rows:
        k = (
            (row.get("ticker") or row.get("issuer_ticker") or "").upper(),
            str(row.get("filer_cik") or "").lstrip("0"),
            row.get("signal_date") or row.get("file_date") or "",
        )
        if not k[0] or not k[1] or not k[2] or k in row_keys:
            continue
        row_keys.add(k)
        deduped_rows.append(row)

    for row in deduped_rows:
        ticker = (row.get("ticker") or row.get("issuer_ticker") or "").upper()
        sched_date = row.get("signal_date") or row.get("file_date") or ""
        filer_cik = str(row.get("filer_cik") or "").lstrip("0")
        filer_name = row.get("filer_name") or ""
        if not ticker or not sched_date or not filer_cik:
            continue
        try:
            sd = date.fromisoformat(sched_date)
        except ValueError:
            continue
        issuer_cik = _issuer_cik_for_ticker(ticker, cik_map)
        if not issuer_cik:
            continue
        start = sd - timedelta(days=lookback_days)
        for hit in _issuer_8k_in_window(issuer_cik, start=start, end=sd):
            acc = hit.get("accession_dashed") or ""
            key = f"{acc}#{ticker}#{filer_cik}"
            if key in seen:
                continue
            text = _filing_text_bundle(issuer_cik, acc, primary_document=hit.get("primary_document") or "")
            if not text or not is_stake_disclosure(text):
                continue
            plain = re.sub(r"<[^>]+>", " ", text).upper()
            fund_ok = False
            for cik, name, pat in patterns:
                if cik == filer_cik and pat in plain:
                    fund_ok = True
                    break
            if not fund_ok:
                continue
            seen.add(key)
            out.append(
                WhaleSignal(
                    signal_id=f"8k_stake#{acc}#{ticker}#{filer_cik}",
                    signal_type="8k_stake",
                    signal_date=hit.get("file_date") or "",
                    filer_cik=filer_cik,
                    filer_name=filer_name,
                    ticker=ticker,
                    alert_class="primary",
                    meta={
                        "schedule_filing_date": sched_date,
                        "lead_days": (sd - date.fromisoformat(hit["file_date"])).days if hit.get("file_date") else None,
                        "source": "issuer_before_schedule",
                    },
                )
            )
    return out


def roster_8k_signals(funds: list[dict], *, years: int = 2) -> list[WhaleSignal]:
    """Fund CIK 8-Ks (rare); kept for completeness."""
    cik_map = company_tickers_map()
    name_map = build_name_ticker_map()
    enriched: list[dict] = []
    for f in funds:
        cik = str(f.get("cik") or "").lstrip("0")
        if not cik:
            continue
        for hit in eight_k_filings_for_cik(cik, years=years):
            hit["display_names"] = [f.get("fund_name") or ""]
            parsed = parse_issuer_8k_stake(hit, funds, cik_map, name_map)
            if parsed:
                enriched.append(parsed)
    return eight_k_to_signals(enriched)
