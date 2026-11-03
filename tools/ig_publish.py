#!/usr/bin/env python3
"""Publish queued Reels to Instagram (Instagram API with Instagram Login).

Queue = one branch per Reel: queue/<id> holding publish.json + video.mp4.
This script scans those branches, waits for each one's slot, publishes it,
records the result in outputs/igpublish/<id>.json on main, and deletes the branch.

publish.json:
  {"id": "...", "account": "bhugol", "file": "video.mp4", "caption": "...",
   "publishAt": null | "2026-11-03T21:00:00+05:30"}

Tokens come only from env IG_TOKEN_<ACCOUNT> (GitHub Secrets). They are sent in the
Authorization header and never printed or written to any file.
"""
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

import requests

API = "https://graph.instagram.com/" + os.environ.get("IG_API_VERSION", "v26.0")
REPO = os.environ["GITHUB_REPOSITORY"]          # owner/name, set by Actions
GH = "https://api.github.com/repos/" + REPO
GH_HDR = {"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
          "Accept": "application/vnd.github+json"}
OUT = pathlib.Path("outputs/igpublish")
WINDOW_MIN = 25          # take items due within this many minutes, then wait for the exact time
POLL_SEC, POLL_MAX = 20, 900


def now():
    return dt.datetime.now(dt.timezone.utc)


def git(*a):
    return subprocess.run(["git", *a], check=True, capture_output=True, text=True).stdout


def token_for(account):
    t = os.environ.get("IG_TOKEN_" + account.upper().replace("-", "_"))
    if not t:
        raise RuntimeError(f"no secret IG_TOKEN_{account.upper()} set")
    return t


class IG:
    def __init__(self, token):
        self.h = {"Authorization": "Bearer " + token}

    def _ok(self, r):
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        if r.status_code >= 400 or "error" in body:
            err = body.get("error", body)
            raise RuntimeError(f"Instagram API {r.status_code}: {json.dumps(err)[:500]}")
        return body

    def get(self, path, **params):
        return self._ok(requests.get(f"{API}/{path}", headers=self.h, params=params, timeout=60))

    def post(self, path, **data):
        return self._ok(requests.post(f"{API}/{path}", headers=self.h, data=data, timeout=120))


def queued():
    items = []
    for line in git("ls-remote", "--heads", "origin", "queue/*").splitlines():
        sha, ref = line.split()
        branch = ref.removeprefix("refs/heads/")
        git("fetch", "-q", "--depth", "1", "origin", f"{ref}:refs/remotes/origin/{branch}")
        job = json.loads(git("show", f"origin/{branch}:publish.json"))
        at = job.get("publishAt")
        when = dt.datetime.fromisoformat(at).astimezone(dt.timezone.utc) if at else now()
        items.append((when, branch, sha, job))
    return sorted(items, key=lambda x: x[0])


def wait_ready(ig, cid):
    t0 = time.time()
    while True:
        st = ig.get(cid, fields="status_code,status").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED") or time.time() - t0 > POLL_MAX:
            raise RuntimeError(f"container {cid} status {st}")
        time.sleep(POLL_SEC)


def release_url(job_id, video_path):
    """Fallback host: a temporary release asset served as video/mp4."""
    rel = requests.post(f"{GH}/releases", headers=GH_HDR, timeout=60, json={
        "tag_name": f"ig-{job_id}", "name": f"ig-{job_id}", "prerelease": True}).json()
    up = rel["upload_url"].split("{")[0]
    with open(video_path, "rb") as f:
        a = requests.post(up, params={"name": "video.mp4"}, data=f, timeout=600,
                          headers={**GH_HDR, "Content-Type": "video/mp4"}).json()
    return rel["id"], a["browser_download_url"]


def drop_release(rel_id, job_id):
    requests.delete(f"{GH}/releases/{rel_id}", headers=GH_HDR, timeout=60)
    requests.delete(f"{GH}/git/refs/tags/ig-{job_id}", headers=GH_HDR, timeout=60)


def publish(branch, sha, job):
    jid, acct = job["id"], job["account"]
    res = {"id": jid, "account": acct, "branch": branch, "started": now().isoformat()}
    ig = IG(token_for(acct))
    me = ig.get("me", fields="user_id,username")
    uid = me["user_id"]
    res["username"] = me.get("username")
    quota = ig.get(f"{uid}/content_publishing_limit", fields="quota_usage,config").get("data", [{}])[0]
    res["quota_before"] = quota

    fields = {"media_type": "REELS", "caption": job.get("caption", "")}
    for k in ("thumb_offset", "share_to_feed"):          # optional pass-throughs only if given
        if k in job:
            fields[k] = job[k]

    vid = job.get("file", "video.mp4")
    raw = f"https://raw.githubusercontent.com/{REPO}/{sha}/{vid}"
    rel_id = None
    try:
        try:
            cid = ig.post(f"{uid}/media", video_url=raw, **fields)["id"]
            wait_ready(ig, cid)
            res["host"] = "raw"
        except RuntimeError as e:
            res["raw_error"] = str(e)
            local = f"/tmp/{jid}.mp4"
            with open(local, "wb") as f:
                f.write(subprocess.run(["git", "show", f"origin/{branch}:{vid}"],
                                       check=True, capture_output=True).stdout)
            rel_id, url = release_url(jid, local)
            cid = ig.post(f"{uid}/media", video_url=url, **fields)["id"]
            wait_ready(ig, cid)
            res["host"] = "release"
        mid = ig.post(f"{uid}/media_publish", creation_id=cid)["id"]
        info = ig.get(mid, fields="permalink,media_product_type,timestamp")
        res.update(status="published", media_id=mid, permalink=info.get("permalink"),
                   media_product_type=info.get("media_product_type"), published=info.get("timestamp"))
    finally:
        if rel_id:
            drop_release(rel_id, jid)
    return res


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    failed = False
    for when, branch, sha, job in queued():
        out = OUT / f"{job['id']}.json"
        prev = json.loads(out.read_text()) if out.exists() else {}
        if prev.get("status") == "published":
            print(f"{job['id']}: already published, removing branch")
            git("push", "-q", "origin", "--delete", branch)
            continue
        if prev.get("status") == "error" and prev.get("sha") == sha:
            print(f"{job['id']}: failed earlier on this exact upload; re-queue to retry")
            continue
        lead = (when - now()).total_seconds()
        if lead > WINDOW_MIN * 60:
            print(f"{job['id']}: due {when.isoformat()}, not yet")
            continue
        if lead > 0:
            print(f"{job['id']}: waiting {int(lead)}s for its slot")
            time.sleep(lead)
        try:
            res = publish(branch, sha, job)
            print(f"{job['id']}: published {res.get('permalink')}")
            git("push", "-q", "origin", "--delete", branch)
        except Exception as e:                       # record and keep the branch for a look
            res = {"id": job["id"], "account": job.get("account"), "status": "error", "sha": sha,
                   "error": str(e)[:800], "at": now().isoformat()}
            print(f"{job['id']}: ERROR {e}", file=sys.stderr)
            failed = True
        out.write_text(json.dumps(res, indent=2) + "\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
