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
* The contract is **closed**: only the summary's own top-level fields are
  accepted, the named blocks must be objects, `transport`/`integration_version`
  must be strings, and nesting deeper than five levels is refused. A scanner
  cannot get a row in without knowing the contract.
* A **coarse per-minute budget** (60 requests, per colo) answers `429` once
  exceeded. It is deliberately not per client — that would need an IP, and this
  Worker stores none. The counter lives in the Cache API for two minutes and
  costs no KV writes.
* The row is stored at `v<schema>/<install_token>/<generated_on>` with a
  **two-year TTL**, so old rows expire without a maintenance job.

`GET /health` — `{"ok": true}`.

`DELETE /install/<install_token>` — erases every row for that installation. The
token in the path **is** the credential: 128 bits, generated locally by the
integration, so only the installation that produced the rows can erase them.
No shared secret is needed, which is why the Worker has no secrets at all.

## Deliberate limits

| Not done | Why |
|---|---|
| Request logging, IP storage | the payload carries no identifiers, and neither should the log |
| Cookies, analytics, third-party calls | nothing to gain, one more processor to disclose |
| Free-text fields | the summary is numbers, booleans and short enums; the intake rejects identifier-shaped keys |
| Auth on `/ingest` | the payload is identifier-free and TTL-bounded; a shared secret in an open-source client is not a secret |
| Auth on `DELETE` beyond the token | the install token is 128 random bits and only selects its own rows |

## Rate limiting

Two layers, and the first one is free:

1. **Inside the Worker** — the per-minute Cache API budget above. Per colo, so
   a distributed flood still gets through, but a single scanner does not.
2. **A zone-level rule** — Cloudflare's rate limiting applies to **zones**, not
   to `*.workers.dev`. To use it, attach a Custom Domain (e.g.
   `telemetry.example.com`) to this Worker and add a rule:

   ```
   (http.host eq "telemetry.example.com"
    and http.request.method eq "POST"
    and http.request.uri.path eq "/ingest")
   counting characteristic: IP · 5 requests / 60 s · Block for 10 min
   ```

   A legitimate installation posts a few times a *week*, so that budget is
   generous. With a custom domain you can also disable the `workers.dev`
   subdomain, which removes most background scanning.

Not worth doing: limiting per `install_token`. An attacker simply rotates the
token, while a real installation never approaches any limit — the token
identifies data, not a client.

`DELETE` needs no limit: it only touches rows whose 128-bit token is presented,
and a wrong token finds nothing.

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
gh secret set CF_API_TOKEN  --body "<token with Workers Scripts + KV edit>"
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
