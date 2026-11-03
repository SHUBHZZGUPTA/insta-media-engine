#!/usr/bin/env python3
"""Weekly refresh of long-lived Instagram tokens (they expire ~60 days after the last refresh).

python3 tools/ig_token.py <account> [<account> ...]
Writes outputs/igtoken/<account>.json with the new expiry only (never the token).
If Instagram hands back a different token string, the secret IG_TOKEN_<ACCOUNT> is
updated through the GitHub API, which needs the IG_SECRETS_PAT secret (a fine-grained
token for this repo with Secrets: read and write). Without it the run fails loudly.
"""
import base64
import datetime as dt
import json
import os
import pathlib
import sys

import requests

out_dir = pathlib.Path("outputs/igtoken")
out_dir.mkdir(parents=True, exist_ok=True)
repo = os.environ["GITHUB_REPOSITORY"]
failed = False


def set_secret(name, value):
    pat = os.environ.get("IG_SECRETS_PAT")
    if not pat:
        raise RuntimeError("token changed but IG_SECRETS_PAT is not set, so the secret can't be updated")
    from nacl import encoding, public
    h = {"Authorization": "Bearer " + pat, "Accept": "application/vnd.github+json"}
    base = f"https://api.github.com/repos/{repo}/actions/secrets"
    key = requests.get(f"{base}/public-key", headers=h, timeout=30).json()
    box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    enc = base64.b64encode(box.encrypt(value.encode())).decode()
    r = requests.put(f"{base}/{name}", headers=h, timeout=30,
                     json={"encrypted_value": enc, "key_id": key["key_id"]})
    if r.status_code not in (201, 204):
        raise RuntimeError(f"secret update failed: HTTP {r.status_code}")


for acct in sys.argv[1:]:
    name = "IG_TOKEN_" + acct.upper().replace("-", "_")
    rec = {"account": acct, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        tok = os.environ.get(name)
        if not tok:
            raise RuntimeError(f"secret {name} not set")
        r = requests.get("https://graph.instagram.com/refresh_access_token", timeout=60,
                         params={"grant_type": "ig_refresh_token", "access_token": tok})
        body = r.json()
        if r.status_code >= 400 or "access_token" not in body:
            raise RuntimeError(f"refresh failed: HTTP {r.status_code} {json.dumps(body.get('error', {}))[:300]}")
        new = body["access_token"]
        rec["expires_in_days"] = round(body.get("expires_in", 0) / 86400, 1)
        rec["token_changed"] = new != tok
        if new != tok:
            print(f"::add-mask::{new}")
            set_secret(name, new)
        rec["ok"] = True
    except Exception as e:
        failed = True
        rec.update(ok=False, error=str(e)[:500])
    (out_dir / f"{acct}.json").write_text(json.dumps(rec, indent=2) + "\n")
    print(json.dumps(rec))
sys.exit(1 if failed else 0)
