"""
lambdas/compute_signals/handler.py

Lambda 2 of 4 in the Market Pulse Step Function.

Reads raw ETF data from DynamoDB and computes:
  - Momentum scores per sector (weighted 1d/5d/20d)
  - Rotation signals (sectors making big moves)
  - Cluster signals (correlated sector groups moving together)
  - Market regime snapshot (risk-on vs risk-off)

Stores signals payload → DynamoDB SK=signals
"""
import os
import math
import statistics
from datetime import datetime, timezone

try:
    from shared.config import (
        SECTOR_ETFS, MACRO_ETFS, SECTOR_CLUSTERS,
        MOMENTUM_WEIGHTS, ROTATION_STD_THRESHOLD, ROTATION_CLUSTER_MIN,
        SK_RAW_DATA, SK_SIGNALS,
    )
    from shared.dynamo import save_report, load_report
except ImportError:
    import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
    from shared.config import (
        SECTOR_ETFS, MACRO_ETFS, SECTOR_CLUSTERS,
        MOMENTUM_WEIGHTS, ROTATION_STD_THRESHOLD, ROTATION_CLUSTER_MIN,
        SK_RAW_DATA, SK_SIGNALS,
    )
    from shared.dynamo import save_report, load_report


# ─────────────────────────────────────────────────────────────────────────────
# Momentum scoring
# ─────────────────────────────────────────────────────────────────────────────

def compute_momentum_score(etf: dict) -> float | None:
    """
    Weighted composite momentum score for one ETF.
    Score = (1d_ret × 0.5) + (5d_ret × 0.3) + (20d_ret × 0.2)
    Returns None if any window is missing.
    """
    r1  = etf.get("return_1d")
    r5  = etf.get("return_5d")
    r20 = etf.get("return_20d")
    if any(v is None for v in [r1, r5, r20]):
        return None
    return (
        r1  * MOMENTUM_WEIGHTS["1d"]  +
        r5  * MOMENTUM_WEIGHTS["5d"]  +
        r20 * MOMENTUM_WEIGHTS["20d"]
    )


def rank_sectors(etf_data: dict) -> list[dict]:
    """
    Compute momentum scores for all sector ETFs and return ranked list.
    """
    scored = []
    for ticker in SECTOR_ETFS:
        etf = etf_data.get(ticker)
        if not etf:
            continue
        score = compute_momentum_score(etf)
        scored.append({
            "ticker":     ticker,
            "name":       etf.get("name", ticker),
            "price":      etf.get("price"),
            "return_1d":  etf.get("return_1d"),
            "return_5d":  etf.get("return_5d"),
            "return_20d": etf.get("return_20d"),
            "momentum_score": round(score, 4) if score is not None else None,
        })
    scored.sort(key=lambda x: x["momentum_score"] or -999, reverse=True)
    return scored


# ─────────────────────────────────────────────────────────────────────────────
# Rotation detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_rotation_signals(ranked_sectors: list[dict]) -> list[dict]:
    """
    Flag sectors whose 1-day move is unusually large relative to the group.

    Method:
      - Compute mean and std of 1d returns across all sectors
      - Any sector > ROTATION_STD_THRESHOLD std above mean = rotation signal
      - Any sector < -ROTATION_STD_THRESHOLD std below mean = outflow signal
    """
    returns_1d = [s["return_1d"] for s in ranked_sectors if s["return_1d"] is not None]
    if len(returns_1d) < 5:
        return []

    mean_1d = statistics.mean(returns_1d)
    std_1d  = statistics.stdev(returns_1d)

    signals = []
    for sector in ranked_sectors:
        r = sector.get("return_1d")
        if r is None or std_1d == 0:
            continue
        z_score = (r - mean_1d) / std_1d
        if abs(z_score) >= ROTATION_STD_THRESHOLD:
            signals.append({
                "ticker":    sector["ticker"],
                "name":      sector["name"],
                "return_1d": r,
                "z_score":   round(z_score, 3),
                "direction": "INFLOW" if z_score > 0 else "OUTFLOW",
                "strength":  "STRONG" if abs(z_score) >= 2.5 else "MODERATE",
            })

    signals.sort(key=lambda x: abs(x["z_score"]), reverse=True)
    return signals


def detect_cluster_signals(etf_data: dict) -> list[dict]:
    """
    Check if correlated sector groups are moving together (macro-driven).
    A cluster fires when ≥ ROTATION_CLUSTER_MIN members all move in the same direction
    and have individually notable moves (|1d return| > 0.5%).
    """
    cluster_signals = []
    for cluster_name, tickers in SECTOR_CLUSTERS.items():
        movers = []
        for t in tickers:
            etf = etf_data.get(t)
            if not etf:
                continue
            r = etf.get("return_1d")
            if r is not None and abs(r) > 0.5:
                movers.append({"ticker": t, "return_1d": r})

        if len(movers) < ROTATION_CLUSTER_MIN:
            continue

        # Check directional consensus
        directions = set("UP" if m["return_1d"] > 0 else "DOWN" for m in movers)
        if len(directions) == 1:
            direction = directions.pop()
            avg_move = statistics.mean(m["return_1d"] for m in movers)
            cluster_signals.append({
                "cluster":    cluster_name,
                "direction":  direction,
                "movers":     movers,
                "avg_move":   round(avg_move, 3),
                "count":      len(movers),
            })

    return cluster_signals


# ─────────────────────────────────────────────────────────────────────────────
# Market regime
# ─────────────────────────────────────────────────────────────────────────────

def assess_market_regime(etf_data: dict) -> dict:
    """
    Quick regime snapshot using classic cross-asset signals.

    Risk-On:  SPY up + TLT flat/down + VIX < 20 + IWM outperforming
    Risk-Off: SPY down + TLT up + VIX > 25 + GLD up
    Mixed:    everything else
    """
    spy  = etf_data.get("SPY",  {}).get("return_1d")
    tlt  = etf_data.get("TLT",  {}).get("return_1d")
    gld  = etf_data.get("GLD",  {}).get("return_1d")
    iwm  = etf_data.get("IWM",  {}).get("return_1d")
    uso  = etf_data.get("USO",  {}).get("return_1d")
    ibit = etf_data.get("IBIT", {}).get("return_1d")
    vix  = etf_data.get("VIX",  {}).get("price")

    risk_on_score  = 0
    risk_off_score = 0

    if spy  is not None: risk_on_score += (1 if spy > 0 else -1)
    if tlt  is not None: risk_off_score += (1 if tlt > 0.3 else 0)
    if gld  is not None: risk_off_score += (1 if gld > 0.3 else 0)
    if iwm  is not None and spy is not None:
        if iwm > spy: risk_on_score += 1    # small caps outperforming = risk-on
    if vix  is not None:
        if vix < 18:  risk_on_score  += 1
        elif vix > 25: risk_off_score += 2
        elif vix > 20: risk_off_score += 1

    if risk_on_score > risk_off_score + 1:
        regime = "RISK-ON"
    elif risk_off_score > risk_on_score + 1:
        regime = "RISK-OFF"
    else:
        regime = "MIXED / NEUTRAL"

    return {
        "regime":          regime,
        "vix":             vix,
        "spy_1d":          spy,
        "tlt_1d":          tlt,
        "gld_1d":          gld,
        "uso_1d":          uso,
        "ibit_1d":         ibit,
        "risk_on_score":   risk_on_score,
        "risk_off_score":  risk_off_score,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Lambda handler
# ─────────────────────────────────────────────────────────────────────────────

def handler(event: dict, context) -> dict:
    date_str = event.get("date")
    print(f"[compute_signals] Running for date: {date_str}")

    # Load raw data from DynamoDB
    raw = load_report(date_str, SK_RAW_DATA)
    if not raw:
        raise ValueError(f"No raw_data found in DynamoDB for {date_str}")

    etf_data = raw.get("etf_data", {})

    # Compute all signals
    ranked_sectors   = rank_sectors(etf_data)
    rotation_signals = detect_rotation_signals(ranked_sectors)
    cluster_signals  = detect_cluster_signals(etf_data)
    market_regime    = assess_market_regime(etf_data)

    # Top movers (all ETFs, not just sectors)
    all_movers = []
    for ticker, etf in etf_data.items():
        r1d = etf.get("return_1d")
        if r1d is not None:
            all_movers.append({"ticker": ticker, "name": etf.get("name", ticker),
                                "return_1d": r1d, "price": etf.get("price")})
    all_movers.sort(key=lambda x: abs(x["return_1d"]), reverse=True)
    top_movers = all_movers[:10]

    signals_payload = {
        "date":              date_str,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "market_regime":     market_regime,
        "ranked_sectors":    ranked_sectors,
        "rotation_signals":  rotation_signals,
        "cluster_signals":   cluster_signals,
        "top_movers":        top_movers,
    }

    save_report(date_str, SK_SIGNALS, signals_payload)

    return {
        "date":             date_str,
        "status":           "ok",
        "regime":           market_regime["regime"],
        "rotation_alerts":  len(rotation_signals),
        "cluster_alerts":   len(cluster_signals),
    }
