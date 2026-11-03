# insta-media-engine

Publishes finished vertical videos as Instagram Reels through the Instagram API (Instagram Login), using GitHub Actions.

| Workflow | Trigger | Does |
|---|---|---|
| `igpublish` | push to `queue/<id>`, every 10 min, manual | Publishes each queued Reel at its slot, records `outputs/igpublish/<id>.json`, deletes the branch |
| `igcheck` | push `jobs/igcheck/<id>.json` to main | Confirms each account's token, username and publishing quota |
| `igtoken` | weekly, manual | Refreshes long-lived tokens before their ~60-day expiry |

## Queue a Reel
Branch `queue/<id>` containing `video.mp4`, a copy of `.github/workflows/igpublish.yml`, and `publish.json`:

```json
{"id": "bhugol-2026-11-03-1", "account": "bhugol", "file": "video.mp4",
 "caption": "…", "publishAt": "2026-11-03T21:00:00+05:30"}
```

`publishAt: null` posts on the next run. Instagram's API has no native scheduling, so the workflow waits for the slot (posts land within a few minutes of it). The video is served to Meta from the branch's raw URL, with a temporary release asset as fallback; both are gone after posting. No AI-content label is set.

## Secrets
- `IG_TOKEN_<ACCOUNT>` — long-lived Instagram user token (e.g. `IG_TOKEN_BHUGOL`)
- `IG_SECRETS_PAT` — optional; fine-grained token for this repo with Secrets read/write, used only if a refresh returns a new token string

Tokens are read from secrets only and never printed or committed.
