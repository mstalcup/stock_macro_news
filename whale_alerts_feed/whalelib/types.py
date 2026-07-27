from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WhaleSignal:
    signal_id: str
    signal_type: str
    signal_date: str
    filer_cik: str
    filer_name: str
    ticker: str
    issuer_name: str = ""
    quarter_end: str = ""
    accession: str = ""
    alert_class: str = "primary"
    meta: dict = field(default_factory=dict)
