"""HTTPS helper — uses AWS combined CA bundle on Windows when present."""
import os
import ssl
from urllib.request import Request, urlopen

_DEFAULT_CA = os.path.join(os.path.expanduser("~"), ".aws", "aws-combined-ca.pem")


def urlopen_json_request(req: Request, *, timeout: int = 30):
    ctx = None
    ca = os.environ.get("AWS_CA_BUNDLE") or (
        _DEFAULT_CA if os.path.isfile(_DEFAULT_CA) else None
    )
    if ca:
        ctx = ssl.create_default_context(cafile=ca)
    return urlopen(req, timeout=timeout, context=ctx)
