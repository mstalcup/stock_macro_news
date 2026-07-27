from __future__ import annotations

import json
import urllib.error
import urllib.request


def post_json(
    url: str,
    *,
    headers: dict[str, str],
    body: dict,
    timeout: int = 120,
) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"HTTP {exc.code} {url}: {err}") from exc
