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
 * and protected by strict shape validation plus a size limit. Erasing an
 * installation needs `ADMIN_TOKEN`, which never ships to clients.
 */

const PAYLOAD_MAX_BYTES = 16 * 1024;
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

function validate(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return "body must be a JSON object";
  }
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
  const auth = request.headers.get("authorization") ?? "";
  if (!env.ADMIN_TOKEN || auth !== `Bearer ${env.ADMIN_TOKEN}`) {
    return json({ error: "unauthorized" }, 401);
  }
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
