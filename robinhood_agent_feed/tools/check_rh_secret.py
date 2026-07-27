"""Print rh-oauth secret metadata (no token values)."""
import json
import boto3

sm = boto3.Session(profile_name="mastalcup", region_name="us-east-1").client("secretsmanager")
d = json.loads(sm.get_secret_value(SecretId="robinhood-agent-feed/rh-oauth")["SecretString"])
for k in ("access_token", "refresh_token", "device_token"):
    v = (d.get(k) or "").strip()
    print(f"{k}: {'set' if v else 'MISSING'} ({len(v)} chars)")
print("client_id:", d.get("client_id") or "MISSING")
print("expires_at:", d.get("expires_at") or "MISSING")
print("user_uuid:", d.get("user_uuid") or "MISSING")
