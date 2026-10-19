# Qvantum summary intake

A Cloudflare Worker that stores the **opt-in, identifier-free** summary the
integration exports (`qvantum.export_summary`) and the weekly uploader (a later
change, `telemetry.py`) posts. It exists so the pooled questions — does the
configured room-compensation factor equal the measured slope, how often does the
cold end bite — can be answered across installations instead of on one.

Nothing is sent unless a user answered **"Bidra"** in the repair prompt.

## Contract

`POST /ingest` — body is the summary JSON plus two request-only fields:

```json
{
  "schema_version": 5,
  "generated_on": "2026-10-19",
  "install_token": "0123456789abcdef0123456789abcdef",
  "...": "the export's own blocks"
}
```

* `install_token` is generated locally by the integration (32 hex characters,
  never derived from the serial number). The **exported file never contains it**
  — the uploader adds it to the request only, so a downloaded summary stays
  identifier-free.
* `schema_version` must be an integer ≥ 5 and `generated_on` an ISO date.
* Bodies over 16 KiB are rejected, values that look like identifiers
  (`entity_id`, `friendly_name`, `latitude`, `longitude`) are refused.
* The row is stored at `v<schema>/<install_token>/<generated_on>` with a
  **two-year TTL**, so old rows expire without a maintenance job.

`GET /health` — `{"ok": true}`.

`DELETE /install/<install_token>` — erases every row for that installation.
Requires `Authorization: Bearer $ADMIN_TOKEN`; the token is a Worker secret and
is never shipped to clients.

## Deliberate limits

| Not done | Why |
|---|---|
| Request logging, IP storage | the payload carries no identifiers, and neither should the log |
| Cookies, analytics, third-party calls | nothing to gain, one more processor to disclose |
| Free-text fields | the summary is numbers, booleans and short enums; the intake rejects identifier-shaped keys |
| Auth on `/ingest` | the payload is identifier-free and TTL-bounded; a shared secret in an open-source client is not a secret |

## Deploy

Deployed by `.github/workflows/telemetry-worker.yml` on pushes that touch this
directory and on releases. It is **not** part of the HACS asset: `whl`/zip builds
take `custom_components/qvantum/` only.

Why a Worker and not a Snippet: Snippets run on the free plan too, but they
have **no bindings** — no KV namespace, no secrets — so they cannot store a
row or gate erasure. A Worker on the **free plan** does: 100 000 requests/day,
and one request per installation per week is nowhere near it. KV's free tier
(1 000 writes/day, 1 GB) is likewise far above a weekly row per install.

One-time setup:

```bash
npx wrangler secret put ADMIN_TOKEN   # only for erasure
gh secret set CF_API_TOKEN  --body "<token with Workers KV edit>"
gh secret set CF_ACCOUNT_ID --body "<account id>"
```

The KV namespace (`1fe27077134347ebb0cc94a099da8866`) is already bound in
`wrangler.toml`.

Without the two repository secrets the deploy step is skipped with a notice, so
forks and unrelated PRs never fail on it.

## Reading the pool

Rows are plain JSON in KV:

```bash
npx wrangler kv key list --binding QVANTUM_SUMMARIES
npx wrangler kv key get "v5/<install_token>/2026-10-19" --binding QVANTUM_SUMMARIES
```

Group by `schema_version` first (the fields change meaning between versions),
then by `device.model` and `plant.sensor_mode`.
