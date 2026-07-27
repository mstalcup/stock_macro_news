import hashlib
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

_STRIP_QUERY_KEYS = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
)


def canonicalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    try:
        p = urlparse(url)
        q = parse_qs(p.query, keep_blank_values=False)
        filtered = {k: v for k, v in q.items() if k.lower() not in _STRIP_QUERY_KEYS}
        new_query = urlencode(filtered, doseq=True)
        path = re.sub(r"/+$", "", p.path) or "/"
        return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", new_query, ""))
    except Exception:
        return url


def article_id_from_url(url: str) -> str:
    canon = canonicalize_url(url)
    if not canon:
        return hashlib.sha256((url or "empty").encode()).hexdigest()[:16]
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def normalize_title(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split())
