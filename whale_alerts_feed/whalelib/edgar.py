from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from .backtest_config import EDGAR_USER_AGENT

LOG = logging.getLogger(__name__)
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
DATA_SEC = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
_last_request = 0.0
_MIN_INTERVAL = 0.12


def _throttle() -> None:
    global _last_request
    elapsed = time.monotonic() - _last_request
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request = time.monotonic()


def _get_json(url: str) -> dict | list | None:
    _throttle()
    req = Request(url, headers={"User-Agent": EDGAR_USER_AGENT, "Accept": "application/json"}, method="GET")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        LOG.warning("edgar fetch failed %s: %r", url, exc)
        return None


def _get_text(url: str) -> str:
    _throttle()
    req = Request(url, headers={"User-Agent": EDGAR_USER_AGENT}, method="GET")
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        LOG.warning("edgar text failed %s: %r", url, exc)
        return ""


def pad_cik(cik: str | int) -> str:
    return str(int(cik)).zfill(10)


def search_filings(
    *,
    forms: list[str],
    start_date: date,
    end_date: date,
    page_size: int = 100,
    max_pages: int = 50,
    q: str = "",
) -> list[dict]:
    hits: list[dict] = []
    for page in range(max_pages):
        params = {
            "forms": ",".join(forms),
            "dateRange": "custom",
            "startdt": start_date.isoformat(),
            "enddt": end_date.isoformat(),
            "from": str(page * page_size),
            "size": str(page_size),
        }
        if q:
            params["q"] = q
        url = f"{EFTS_URL}?{urlencode(params)}"
        data = _get_json(url)
        if not data or not isinstance(data, dict):
            break
        batch = data.get("hits", {}).get("hits") or []
        if not batch:
            break
        for h in batch:
            src = h.get("_source") or {}
            ciks = src.get("ciks") or []
            names = src.get("display_names") or []
            root_forms = src.get("root_forms") or []
            form = src.get("file_type") or (root_forms[0] if root_forms else "")
            nodash, dashed = normalize_accession(h.get("_id") or "")
            hits.append(
                {
                    "accession": nodash,
                    "accession_dashed": dashed,
                    "file_date": (src.get("file_date") or "")[:10],
                    "form": form,
                    "filer_cik": str(ciks[0]).lstrip("0") if ciks else "",
                    "filer_name": names[0] if names else "",
                    "display_names": list(names),
                    "ciks": [str(c).lstrip("0") for c in ciks],
                    "period_ending": (src.get("period_ending") or "")[:10],
                }
            )
        total = (data.get("hits", {}).get("total") or {}).get("value", 0)
        if (page + 1) * page_size >= total:
            break
    return hits


_TICKER_IN_NAME = re.compile(r"\(([A-Z]{1,5})\)\s*\(CIK\s*0*(\d+)", re.I)
_CIK_IN_NAME = re.compile(r"\(CIK\s*0*(\d+)\)", re.I)
_HREF_RE = re.compile(r'href="([^"]+)"', re.I)


def normalize_accession(adsh: str) -> tuple[str, str]:
    base = (adsh or "").split(":")[0].strip()
    nodash = base.replace("-", "")
    if len(nodash) >= 18:
        dashed = f"{nodash[:10]}-{nodash[10:12]}-{nodash[12:18]}"
    else:
        dashed = base
    return nodash, dashed


def filer_cik_from_accession(adsh: str) -> str:
    """SEC accession prefix is the filing entity CIK."""
    dashed = normalize_accession(adsh)[1]
    prefix = dashed.split("-")[0] if dashed else ""
    try:
        return str(int(prefix)).lstrip("0") or "0"
    except ValueError:
        return ""


def ticker_from_display_name(name: str) -> str:
    m = _TICKER_IN_NAME.search(name or "")
    return m.group(1).upper() if m else ""


def issuer_cik_from_display_name(name: str) -> str:
    m = _TICKER_IN_NAME.search(name or "")
    if m:
        return m.group(2).lstrip("0")
    mc = _CIK_IN_NAME.search(name or "")
    return str(int(mc.group(1))).lstrip("0") if mc else ""


def issuer_cik_from_hit(hit: dict) -> str:
    for n in hit.get("display_names") or [hit.get("filer_name") or ""]:
        cik = issuer_cik_from_display_name(n)
        if cik:
            return cik
    for c in hit.get("ciks") or []:
        c = str(c).lstrip("0")
        if c:
            return c
    return filer_cik_from_accession(hit.get("accession_dashed") or hit.get("accession") or "")


def parse_schedule_entities(hit: dict) -> dict:
    """Split issuer (target stock) vs filer (investor) on 13D/G search hits."""
    out = dict(hit)
    issuer_ticker = ""
    issuer_cik = ""
    filer_cik = ""
    filer_name = ""
    for n in out.get("display_names") or [out.get("filer_name") or ""]:
        t = ticker_from_display_name(n)
        if t:
            issuer_ticker = t
            issuer_cik = issuer_cik_from_display_name(n)
            continue
        clean = re.sub(r"\s*\(CIK\s*0*(\d+)\)\s*", "", n, flags=re.I).strip()
        if clean and not filer_name:
            filer_name = clean
            mc = re.search(r"\(CIK\s*0*(\d+)\)", n, re.I)
            if mc:
                filer_cik = mc.group(1).lstrip("0")
    ciks = out.get("ciks") or []
    if not filer_cik:
        for c in ciks:
            c = str(c).lstrip("0")
            if c and c != issuer_cik:
                filer_cik = c
                break
    if not filer_cik:
        filer_cik = filer_cik_from_accession(out.get("accession_dashed") or out.get("accession") or "")
    if not filer_cik and ciks:
        filer_cik = str(ciks[-1]).lstrip("0")
    if filer_cik and issuer_cik and filer_cik == issuer_cik:
        acc_cik = filer_cik_from_accession(out.get("accession_dashed") or out.get("accession") or "")
        if acc_cik and acc_cik != issuer_cik:
            filer_cik = acc_cik
    if not filer_name or ticker_from_display_name(filer_name):
        filer_name = filer_name_from_hit(out) or (f"CIK {filer_cik}" if filer_cik else "")
    nodash, dashed = normalize_accession(out.get("accession_dashed") or out.get("accession") or "")
    out["accession"] = nodash
    out["accession_dashed"] = dashed
    out["issuer_ticker"] = issuer_ticker or out.get("issuer_ticker") or ""
    out["issuer_cik"] = issuer_cik
    out["filer_cik"] = filer_cik
    out["filer_name"] = filer_name
    form = (out.get("form") or "").upper()
    if "13D" in form and "/A" not in form:
        out["signal_type"] = "13d_new"
    elif "13D" in form:
        out["signal_type"] = "13d_increase"
    elif "13G" in form and "/A" not in form:
        out["signal_type"] = "13g_new"
    elif "13G" in form:
        out["signal_type"] = "13g_increase"
    else:
        out["signal_type"] = out.get("signal_type") or "schedule_other"
    out["alert_class"] = "primary"
    return out


def filer_name_from_hit(hit: dict) -> str:
    names = hit.get("display_names") or []
    for n in names:
        if not ticker_from_display_name(n):
            clean = re.sub(r"\s*\(CIK\s*\d+\)\s*", "", n).strip()
            if clean:
                return clean
    raw = hit.get("filer_name") or ""
    return re.sub(r"\s*\([A-Z]{1,5}\)\s*\(CIK.*", "", raw).strip() or raw


def company_tickers_map() -> dict[str, str]:
    data = _get_json(f"{SEC_WWW}/files/company_tickers.json")
    out: dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    for row in data.values():
        if not isinstance(row, dict):
            continue
        cik = str(row.get("cik_str", "")).lstrip("0")
        tick = (row.get("ticker") or "").upper()
        if cik and tick:
            out[cik] = tick
    return out


def submissions_for_cik(cik: str) -> dict | None:
    cik10 = pad_cik(cik)
    data = _get_json(f"{DATA_SEC}/submissions/CIK{cik10}.json")
    return data if isinstance(data, dict) else None


SCHEDULE_FORMS = frozenset({"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"})


def all_schedule_filings_for_cik(cik: str, *, years: int = 2) -> list[dict]:
    """All 13D/G from SEC submissions recent history (~1yr+, up to 1000 filings)."""
    subs = submissions_for_cik(cik)
    if not subs:
        return []
    name = (subs.get("name") or "").strip()
    recent = subs.get("filings", {}).get("recent") or {}
    accession = recent.get("accessionNumber") or []
    filing_date = recent.get("filingDate") or []
    form = recent.get("form") or []
    cutoff = (date.today() - timedelta(days=years * 365)).isoformat()
    out: list[dict] = []
    for i, acc in enumerate(accession):
        if i >= len(form) or i >= len(filing_date):
            break
        fd = (filing_date[i] or "")[:10]
        if fd and fd < cutoff:
            continue
        f = (form[i] or "").upper()
        if not (f in SCHEDULE_FORMS or "13D" in f or "13G" in f):
            continue
        if "13F" in f:
            continue
        nodash, dashed = normalize_accession(acc)
        primary = ""
        recent_primary = recent.get("primaryDocument") or []
        if i < len(recent_primary):
            primary = (recent_primary[i] or "").strip()
        out.append(
            {
                "accession": nodash,
                "accession_dashed": dashed,
                "file_date": fd,
                "form": form[i],
                "filer_cik": str(cik).lstrip("0"),
                "filer_name": name,
                "display_names": [name],
                "ciks": [str(cik)],
                "primary_document": primary,
            }
        )
    return out


def recent_filings_for_cik(cik: str, *, forms: set[str], limit: int = 40) -> list[dict]:
    """Recent filings from submissions API (roster-scoped poll)."""
    subs = submissions_for_cik(cik)
    if not subs:
        return []
    name = (subs.get("name") or "").strip()
    recent = subs.get("filings", {}).get("recent") or {}
    accession = recent.get("accessionNumber") or []
    filing_date = recent.get("filingDate") or []
    form = recent.get("form") or []
    out: list[dict] = []
    for i, acc in enumerate(accession):
        if i >= len(form) or i >= len(filing_date):
            break
        f = (form[i] or "").upper()
        if f not in forms and not any(x in f for x in forms):
            continue
        nodash, dashed = normalize_accession(acc)
        out.append(
            {
                "accession": nodash,
                "accession_dashed": dashed,
                "file_date": (filing_date[i] or "")[:10],
                "form": form[i],
                "filer_cik": str(cik).lstrip("0"),
                "filer_name": name,
                "display_names": [name],
                "ciks": [str(cik)],
            }
        )
        if len(out) >= limit:
            break
    return out


def company_name_for_cik(cik: str) -> str:
    subs = submissions_for_cik(cik)
    if subs:
        return (subs.get("name") or "").strip()
    return ""


def build_name_ticker_map() -> dict[str, str]:
    data = _get_json(f"{SEC_WWW}/files/company_tickers.json")
    out: dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    for row in data.values():
        if not isinstance(row, dict):
            continue
        title = (row.get("title") or "").upper().strip()
        tick = (row.get("ticker") or "").upper()
        if title and tick:
            out[title] = tick
    return out


def filing_index(cik: str, accession: str) -> dict | None:
    _, dashed = normalize_accession(accession)
    acc_nodash = dashed.replace("-", "")
    for raw in (str(cik).lstrip("0"), filer_cik_from_accession(accession)):
        if not raw:
            continue
        cik10 = pad_cik(raw)
        data = _get_json(f"{DATA_SEC}/Archives/edgar/data/{int(cik10)}/{acc_nodash}/index.json")
        if isinstance(data, dict):
            return data
    return None


def _archive_base(cik: str, accession: str) -> tuple[str, str]:
    _, dashed = normalize_accession(accession)
    acc_nodash = dashed.replace("-", "")
    cik10 = pad_cik(str(cik).lstrip("0") or filer_cik_from_accession(accession))
    return cik10, f"{SEC_WWW}/Archives/edgar/data/{int(cik10)}/{acc_nodash}"


def _index_htm_doc_names(cik: str, accession: str) -> list[str]:
    """Fallback when data.sec.gov index.json 404s."""
    _, dashed = normalize_accession(accession)
    acc_nodash = dashed.replace("-", "")
    cik10 = pad_cik(str(cik).lstrip("0"))
    url = f"{SEC_WWW}/Archives/edgar/data/{int(cik10)}/{acc_nodash}/{dashed}-index.htm"
    html = _get_text(url)
    if not html:
        return []
    blocked = ("companysearch", "index.htm", "xsl", ".css", "javascript")
    primary: list[str] = []
    exhibits: list[str] = []
    other: list[str] = []
    for m in _HREF_RE.finditer(html):
        href = m.group(1)
        if href.startswith("http") or ".." in href:
            continue
        name = href.rsplit("/", 1)[-1]
        low = name.lower()
        if not name.endswith((".htm", ".html", ".txt", ".xml")):
            continue
        if any(b in low for b in blocked):
            continue
        if low.endswith(".txt") and acc_nodash in low.replace("-", ""):
            primary.append(name)
        elif "ex99" in low or "ex-99" in low or "exhibit" in low:
            exhibits.append(name)
        elif "form8" in low or "8-k" in low or "8k" in low:
            primary.append(name)
        elif low.startswith("d") and "ex" in low:
            exhibits.append(name)
        elif "ex" in low and any(ch.isdigit() for ch in low):
            exhibits.append(name)
        else:
            primary.append(name)
    out = primary + exhibits + other
    # de-dupe preserve order
    seen: set[str] = set()
    deduped: list[str] = []
    for n in out:
        if n not in seen:
            seen.add(n)
            deduped.append(n)
    return deduped


def filing_doc_urls(
    cik: str,
    accession: str,
    prefer: tuple[str, ...] = (),
    *,
    primary_document: str = "",
    limit: int = 5,
) -> list[str]:
    """Ordered document URLs for a filing (primary + exhibits)."""
    if primary_document:
        _, base = _archive_base(cik, accession)
        return [f"{base}/{primary_document}"]
    candidates: list[str] = []
    for try_cik in (str(cik).lstrip("0"), filer_cik_from_accession(accession)):
        if not try_cik:
            continue
        idx = filing_index(try_cik, accession)
        _, base = _archive_base(try_cik, accession)
        names: list[str] = []
        if idx:
            items = idx.get("directory", {}).get("item") or []
            if not isinstance(items, list):
                items = [items]
            names = [it.get("name", "") for it in items if isinstance(it, dict)]
        if not names:
            names = _index_htm_doc_names(try_cik, accession)
        ordered: list[str] = []
        for pref in prefer:
            for n in names:
                if pref.lower() in n.lower() and n not in ordered:
                    ordered.append(n)
        for n in names:
            if n.endswith((".htm", ".html", ".txt", ".xml")) and n not in ordered:
                ordered.append(n)
        for n in ordered[:limit]:
            candidates.append(f"{base}/{n}")
        if candidates:
            return candidates
    return candidates


def _pick_doc_url(
    cik: str,
    accession: str,
    prefer: tuple[str, ...] = (),
    *,
    primary_document: str = "",
) -> str | None:
    urls = filing_doc_urls(
        cik, accession, prefer, primary_document=primary_document, limit=1
    )
    return urls[0] if urls else None


_ISSUER_RE = re.compile(r"(?:issuerTradingSymbol|issuerSymbol)[^>]*>([A-Z]{1,5})<", re.I)
_ISSUER_CIK_RE = re.compile(r"<issuerCIK[^>]*>(\d+)</issuerCIK>", re.I)
_NAME_RE = re.compile(r"<nameOfIssuer>([^<]+)</nameOfIssuer>", re.I)
_HTML_ISSUER_RE = re.compile(r"([A-Z0-9][A-Z0-9 .,&'\-/]{2,80})\s*\(Name of Issuer\)", re.I)
_TRADING_SYM_RE = re.compile(r"Trading Symbol[:\s]+([A-Z]{1,5})\b", re.I)


def _lookup_ticker_by_name(name: str, name_map: dict[str, str]) -> str:
    up = (name or "").upper().strip()
    if not up:
        return ""
    if up in name_map:
        return name_map[up]
    for k, t in name_map.items():
        if up in k or k in up:
            return t
    return ""


def parse_issuer_ticker_from_text(
    text: str,
    cik_map: dict[str, str],
    name_map: dict[str, str] | None = None,
) -> tuple[str, str]:
    m = _ISSUER_RE.search(text)
    if m:
        return m.group(1).upper(), ""
    mt = _TRADING_SYM_RE.search(text)
    if mt:
        return mt.group(1).upper(), ""
    mc = _ISSUER_CIK_RE.search(text)
    if mc:
        cik = str(int(mc.group(1)))
        if cik in cik_map:
            return cik_map[cik], ""
    issuer_name = ""
    mn = _NAME_RE.search(text)
    if mn:
        issuer_name = mn.group(1).strip()
    if not issuer_name:
        plain = re.sub(r"<[^>]+>", " ", text)
        plain = re.sub(r"\s+", " ", plain)
        mh = _HTML_ISSUER_RE.search(plain)
        if mh:
            issuer_name = mh.group(1).strip()
    if issuer_name and name_map:
        tick = _lookup_ticker_by_name(issuer_name, name_map)
        if tick:
            return tick, issuer_name
    return "", issuer_name


def enrich_schedule_filing(hit: dict, cik_map: dict[str, str], name_map: dict[str, str]) -> dict:
    out = parse_schedule_entities(hit)
    acc = out.get("accession_dashed") or ""
    cik = out.get("filer_cik") or ""
    url = f"{SEC_WWW}/cgi-bin/viewer?action=view&cik={cik}&accession_number={acc}&xbrl_type=v" if cik and acc else ""
    if not out.get("issuer_ticker") and cik and acc:
        primary = (hit.get("primary_document") or out.get("primary_document") or "").strip()
        doc_url = _pick_doc_url(cik, acc, (".xml", ".txt", ".htm"), primary_document=primary)
        if doc_url:
            text = _get_text(doc_url)
            ticker, issuer_name = parse_issuer_ticker_from_text(text, cik_map, name_map)
            if ticker:
                out["issuer_ticker"] = ticker
            if issuer_name:
                out["issuer_name"] = issuer_name
            url = doc_url
    out["source_url"] = url
    return out


def parse_13f_holdings_xml(xml_text: str) -> list[dict]:
    rows: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return rows
    for info in root.iter():
        tag = info.tag.split("}")[-1] if "}" in info.tag else info.tag
        if tag.lower() not in ("infotable", "infoTable"):
            continue
        row: dict[str, str] = {}
        for child in info:
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag.lower() == "shrsorprnamt":
                for sub in child:
                    stag = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                    if stag.lower() == "sshprnamt":
                        row["sshPrnamt"] = (sub.text or "").strip()
                    elif stag.lower() == "sshprnamttype":
                        row["sshPrnamtType"] = (sub.text or "").strip()
            elif ctag.lower() == "votingauthority":
                continue
            else:
                row[ctag] = (child.text or "").strip()
        if row:
            rows.append(row)
    return rows


def fetch_13f_holdings(cik: str, accession: str) -> list[dict]:
    """Parse 13F InfoTable XML — prefer standalone infotable.xml over primary HTML."""
    _, base = _archive_base(cik, accession)
    for try_cik in (str(cik).lstrip("0"), filer_cik_from_accession(accession)):
        if not try_cik:
            continue
        _, try_base = _archive_base(try_cik, accession)
        for name in ("infotable.xml", "InfoTable.xml", "form13fInfoTable.xml"):
            rows = parse_13f_holdings_xml(_get_text(f"{try_base}/{name}"))
            if rows:
                return rows
        for try_cik2 in (try_cik,):
            names = _index_htm_doc_names(try_cik2, accession)
            for n in names:
                low = n.lower()
                if not low.endswith(".xml") or "primary" in low:
                    continue
                if "infotable" in low or "13f" in low or "13fq" in low:
                    rows = parse_13f_holdings_xml(_get_text(f"{try_base}/{n}"))
                    if rows:
                        return rows
    for url in filing_doc_urls(cik, accession, ("infotable.xml",), limit=5):
        if "infotable" in url.lower():
            rows = parse_13f_holdings_xml(_get_text(url))
            if rows:
                return rows
    return []


def issuer_ticker_from_13f_row(row: dict, name_map: dict[str, str]) -> str:
    name = (row.get("nameOfIssuer") or row.get("nameofissuer") or "").upper().strip()
    if not name:
        return ""
    if name in name_map:
        return name_map[name]
    for k, t in name_map.items():
        if name in k or k in name:
            return t
    return ""
