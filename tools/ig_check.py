#!/usr/bin/env python3
"""Token check: python3 tools/ig_check.py <job.json> <out.json>
job: {"id": "...", "accounts": ["bhugol"]}. Writes username, account type and publishing quota."""
import json
import sys

sys.path.insert(0, "tools")
from ig_publish import IG, token_for  # noqa: E402

job = json.load(open(sys.argv[1]))
report = {"id": job["id"], "accounts": {}}
ok = True
for acct in job["accounts"]:
    try:
        ig = IG(token_for(acct))
        me = ig.get("me", fields="user_id,username,account_type,media_count")
        q = ig.get(f"{me['user_id']}/content_publishing_limit", fields="quota_usage,config")
        report["accounts"][acct] = {"ok": True, "username": me.get("username"),
                                    "account_type": me.get("account_type"),
                                    "media_count": me.get("media_count"),
                                    "quota": q.get("data", [{}])[0]}
    except Exception as e:
        ok = False
        report["accounts"][acct] = {"ok": False, "error": str(e)[:500]}
json.dump(report, open(sys.argv[2], "w"), indent=2)
print(json.dumps(report, indent=2))
sys.exit(0 if ok else 1)
