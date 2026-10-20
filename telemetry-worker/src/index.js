/**
 * Qvantum summary intake.
 *
 * Receives the opt-in, identifier-free summary the integration exports
 * (`qvantum.export_summary`) and stores it in KV: one key per installation,
 * schema version and generated date, with a TTL so stale rows disappear on
 * their own.
 *
 * What this Worker deliberately does not do:
 *  - no request logging and no IP storage (the code never reads headers beyond
 *    the admin bearer token);
 *  - no parsing beyond the shape check below, so free text cannot be smuggled
 *    in and reflected anywhere;
 *  - no analytics, no cookies, no third-party calls.
 *
 * Auth: the payload is identifier-free by construction, so the intake is open
 * and protected by strict shape validation plus a size limit. Erasure needs the
 * installation's own token in the path — a 128-bit value generated locally by
 * the integration, so it works as the capability: whoever holds it may delete
 * those rows, and nobody else can.
 */

const PAYLOAD_MAX_BYTES = 16 * 1024;
/** Requests per minute across the whole Worker (per colo, see README). */
const DEFAULT_RATE_LIMIT = 60;
/** Containers deeper than this are refused: the summary is four levels deep. */
const MAX_DEPTH = 5;

/** The only top-level fields the summary may carry; anything else is refused. */
const ALLOWED_TOP_LEVEL = new Set([
  "schema_version",
  "integration_version",
  "generated_on",
  "transport",
  "device",
  "plant",
  "climate",
  "curve",
  "counters",
  "fitted",
  "verdicts",
  "install_token",
]);

/** Blocks that must be objects when present. */
const OBJECT_BLOCKS = [
  "device",
  "plant",
  "climate",
  "curve",
  "counters",
  "fitted",
  "verdicts",
];

/** Scalars that must be strings when present. */
const STRING_FIELDS = ["integration_version", "transport"];
/** Two years: enough for a seasonal comparison, short enough to forget. */
const ROW_TTL_SECONDS = 730 * 24 * 3600;
const INSTALL_TOKEN = /^[a-f0-9]{32}$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
/** Oldest summary schema this intake accepts. */
const SUPPORTED_SCHEMA_MIN = 5;

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });

const rowKey = (schema, install, day) => `v${schema}/${install}/${day}`;

/** Text that must never reach storage, whatever the client sent. */
const FORBIDDEN = ["entity_id", "friendly_name", "latitude", "longitude"];

function depth(value, level = 1) {
  if (value === null || typeof value !== "object") return level;
  const children = Array.isArray(value) ? value : Object.values(value);
  let deepest = level;
  for (const child of children) {
    deepest = Math.max(deepest, depth(child, level + 1));
  }
  return deepest;
}

function validate(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return "body must be a JSON object";
  }
  // The contract is closed: an unexpected key means the client thinks this
  // intake does more than it does, and free-form text is exactly what the
  // whitelist exists to prevent.
  for (const key of Object.keys(payload)) {
    if (!ALLOWED_TOP_LEVEL.has(key)) return `unexpected field: ${key}`;
  }
  for (const block of OBJECT_BLOCKS) {
    const value = payload[block];
    if (value === undefined) continue;
    if (value === null) continue;
    if (typeof value !== "object" || Array.isArray(value)) {
      return `${block} must be an object`;
    }
  }
  for (const field of STRING_FIELDS) {
    if (payload[field] !== undefined && typeof payload[field] !== "string") {
      return `${field} must be a string`;
    }
  }
  if (depth(payload) > MAX_DEPTH) return "payload nested too deeply";
  const schema = payload.schema_version;
  if (!Number.isInteger(schema) || schema < SUPPORTED_SCHEMA_MIN) {
    return `schema_version must be an integer >= ${SUPPORTED_SCHEMA_MIN}`;
  }
  if (!ISO_DATE.test(payload.generated_on ?? "")) {
    return "generated_on must be an ISO date";
  }
  if (!INSTALL_TOKEN.test(payload.install_token ?? "")) {
    return "install_token must be 32 hex characters";
  }
  const flat = JSON.stringify(payload).toLowerCase();
  for (const word of FORBIDDEN) {
    if (flat.includes(word)) return `payload must not contain ${word}`;
  }
  return null;
}

/**
 * A coarse, per-colo minute budget. Deliberately not per client: that would
 * need an identifier (an IP), and this Worker stores none. It stops the common
 * scanner flood; a distributed one needs a zone-level rate limit, which
 * ``*.workers.dev`` cannot have (see README).
 */
async function throttled(env) {
  const cache = globalThis.caches?.default;
  if (!cache) return false; // local runs and tests: fail open
  const limit = Number(env.RATE_LIMIT_PER_MINUTE ?? DEFAULT_RATE_LIMIT);
  const minute = new Date().toISOString().slice(0, 16);
  const key = `https://ratelimit.invalid/${minute}`;
  try {
    const hit = await cache.match(key);
    const count = hit ? Number(await hit.text()) || 0 : 0;
    if (count >= limit) return true;
    await cache.put(
      key,
      new Response(String(count + 1), { headers: { "cache-control": "max-age=120" } }),
    );
  } catch {
    // A broken cache must never block a legitimate upload.
  }
  return false;
}

async function ingest(request, env) {
  const raw = await request.text();
  if (raw.length > PAYLOAD_MAX_BYTES) return json({ error: "payload too large" }, 413);

  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return json({ error: "invalid json" }, 400);
  }

  const problem = validate(payload);
  if (problem) return json({ error: problem }, 400);

  const key = rowKey(payload.schema_version, payload.install_token, payload.generated_on);
  await env.QVANTUM_SUMMARIES.put(key, raw, { expirationTtl: ROW_TTL_SECONDS });
  return json({ stored: key });
}

async function erase(request, env, install) {
  if (!INSTALL_TOKEN.test(install)) return json({ error: "bad install token" }, 400);

  let cursor;
  let removed = 0;
  do {
    const page = await env.QVANTUM_SUMMARIES.list({ cursor });
    cursor = page.list_complete ? undefined : page.cursor;
    for (const entry of page.keys) {
      const parts = entry.name.split("/");
      if (parts.length === 3 && parts[1] === install) {
        await env.QVANTUM_SUMMARIES.delete(entry.name);
        removed += 1;
      }
    }
  } while (cursor);

  return json({ removed });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "POST" && url.pathname === "/ingest") {
      if (await throttled(env)) return json({ error: "too many requests" }, 429);
      return ingest(request, env);
    }
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true });
    }
    if (request.method === "DELETE" && url.pathname.startsWith("/install/")) {
      return erase(request, env, url.pathname.slice("/install/".length));
    }
    return json({ error: "not found" }, 404);
  },
};
