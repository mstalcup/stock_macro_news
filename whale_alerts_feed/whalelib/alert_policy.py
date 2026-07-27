"""Live alert filters validated via tools/sweep_strategies.py on curated backtest."""
from __future__ import annotations

# High-volume passive 13G filers — backtest drag; skip their 13G alerts.
VOLUME_FILER_CIKS = frozenset({"1273087", "1603466"})  # Millennium, Point72

# Fund-specific rules (CIK -> allowed signal types). Empty = use defaults.
FUND_SIGNAL_ALLOWLIST: dict[str, frozenset[str]] = {
    # Greenlight: 13G filings showed +4% med alpha @20d in backtest (n=74).
    "1364742": frozenset({"13g_new", "13g_increase"}),
    # Starboard: new activist 13D only (+2% med @20d, strong 5d pop).
    "1517137": frozenset({"13d_new"}),
}

# Default for roster activist funds: 13D only (drop passive 13G noise).
DEFAULT_ACTIVIST_TYPES = frozenset({"13d_new", "13d_increase"})

# Berkshire 13G is passive portfolio disclosure — confirmation track only.
PASSIVE_ONLY_CIKS = frozenset({"1067983"})


def allowed_signal_types(filer_cik: str, *, tier: str = "") -> frozenset[str] | None:
    """Return allowed signal types, or None if this fund should not alert."""
    cik = str(filer_cik or "").lstrip("0")
    if not cik:
        return None
    if cik in FUND_SIGNAL_ALLOWLIST:
        return FUND_SIGNAL_ALLOWLIST[cik]
    if cik in PASSIVE_ONLY_CIKS:
        return None
    if cik in VOLUME_FILER_CIKS:
        return DEFAULT_ACTIVIST_TYPES  # 13D only if any; skip 13G
    return DEFAULT_ACTIVIST_TYPES


def should_alert(hit: dict, *, tier: str = "") -> bool:
    """Whether to publish a Discord alert for this enriched filing hit."""
    cik = str(hit.get("filer_cik") or "").lstrip("0")
    st = hit.get("signal_type") or ""
    if not cik or not st:
        return False
    allowed = allowed_signal_types(cik, tier=tier)
    if not allowed:
        return False
    if st not in allowed:
        return False
    if cik in VOLUME_FILER_CIKS and st.startswith("13g"):
        return False
    return True
