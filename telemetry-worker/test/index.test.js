import test from "node:test";
import assert from "node:assert/strict";

import worker from "../src/index.js";

class FakeKV {
  constructor() {
    this.map = new Map();
  }
  async put(key, value, options) {
    this.map.set(key, { value, options });
  }
  async list({ cursor } = {}) {
    return {
      keys: [...this.map.keys()].map((name) => ({ name })),
      list_complete: true,
      cursor: undefined,
    };
  }
  async delete(key) {
    this.map.delete(key);
  }
}

const env = (extra = {}) => ({ QVANTUM_SUMMARIES: new FakeKV(), ...extra });
const token = "a".repeat(32);
const payload = (over = {}) => ({
  schema_version: 5,
  generated_on: "2026-10-19",
  install_token: token,
  ...over,
});

const post = (body) =>
  new Request("https://worker.test/ingest", {
    method: "POST",
    body: typeof body === "string" ? body : JSON.stringify(body),
  });

test("stores a valid summary under a schema/install/date key with a TTL", async () => {
  const e = env();
  const response = await worker.fetch(post(payload()), e);

  assert.equal(response.status, 200);
  const key = `v5/${token}/2026-10-19`;
  assert.deepEqual(await response.json(), { stored: key });
  const row = e.QVANTUM_SUMMARIES.map.get(key);
  assert.ok(row.value.includes('"schema_version":5'));
  assert.equal(row.options.expirationTtl, 730 * 24 * 3600);
});

test("rejects an older schema version", async () => {
  const response = await worker.fetch(post(payload({ schema_version: 4 })), env());
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /schema_version/);
});

test("rejects a timestamp instead of a date", async () => {
  const response = await worker.fetch(
    post(payload({ generated_on: "2026-10-19T12:00:00Z" })),
    env(),
  );
  assert.equal(response.status, 400);
});

test("rejects a token that is not 32 hex characters", async () => {
  for (const bad of ["", "ABCDEF", "z".repeat(32), token + "0"]) {
    const response = await worker.fetch(post(payload({ install_token: bad })), env());
    assert.equal(response.status, 400, bad);
  }
});

test("refuses identifier-shaped content", async () => {
  const response = await worker.fetch(
    post(payload({ plant: { entity_id: "sensor.anything" } })),
    env(),
  );
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /entity_id/);
});

test("rejects oversized and malformed bodies", async () => {
  const big = await worker.fetch(post("x".repeat(16 * 1024 + 1)), env());
  assert.equal(big.status, 413);
  const broken = await worker.fetch(post("{not json"), env());
  assert.equal(broken.status, 400);
});

test("health answers without touching storage", async () => {
  const response = await worker.fetch(
    new Request("https://worker.test/health"),
    env(),
  );
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true });
});

test("erasure needs the install token and only drops that installation", async () => {
  const e = env();
  await worker.fetch(post(payload()), e);
  await worker.fetch(post(payload({ install_token: "b".repeat(32) })), e);

  const malformed = await worker.fetch(
    new Request("https://worker.test/install/short", { method: "DELETE" }),
    e,
  );
  assert.equal(malformed.status, 400);
  assert.equal(e.QVANTUM_SUMMARIES.map.size, 2);

  const erased = await worker.fetch(
    new Request(`https://worker.test/install/${token}`, { method: "DELETE" }),
    e,
  );
  assert.equal(erased.status, 200);
  assert.deepEqual(await erased.json(), { removed: 1 });
  assert.deepEqual([...e.QVANTUM_SUMMARIES.map.keys()], [
    `v5/${"b".repeat(32)}/2026-10-19`,
  ]);
});

class FakeCache {
  constructor() {
    this.map = new Map();
  }
  async match(key) {
    const value = this.map.get(String(key));
    return value === undefined ? undefined : new Response(String(value));
  }
  async put(key, response) {
    this.map.set(String(key), Number(await response.text()));
  }
}

function withCache(limit) {
  const cache = new FakeCache();
  globalThis.caches = { default: cache };
  return { cache, env: env({ RATE_LIMIT_PER_MINUTE: limit }) };
}

test("throttles after the per-minute budget and says 429", async () => {
  const { env: e } = withCache(2);
  try {
    const first = await worker.fetch(post(payload()), e);
    const second = await worker.fetch(post(payload({ generated_on: "2026-10-18" })), e);
    const third = await worker.fetch(post(payload({ generated_on: "2026-10-17" })), e);

    assert.equal(first.status, 200);
    assert.equal(second.status, 200);
    assert.equal(third.status, 429);
    assert.deepEqual(await third.json(), { error: "too many requests" });
  } finally {
    delete globalThis.caches;
  }
});

test("fails open when there is no cache (local runs, tests)", async () => {
  delete globalThis.caches;
  for (let i = 0; i < 5; i += 1) {
    const response = await worker.fetch(post(payload()), env());
    assert.equal(response.status, 200);
  }
});

test("rejects an unknown top-level field", async () => {
  const response = await worker.fetch(post(payload({ note: "hello" })), env());
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /unexpected field: note/);
});

test("rejects a block that is not an object", async () => {
  const response = await worker.fetch(post(payload({ plant: "sensor_mode=4" })), env());
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /plant must be an object/);
});

test("rejects a string field carrying a number", async () => {
  const response = await worker.fetch(post(payload({ transport: 4 })), env());
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /transport must be a string/);
});

test("rejects nesting deeper than the contract allows", async () => {
  let nested = { value: 1 };
  for (let i = 0; i < 6; i += 1) nested = { level: nested };
  const response = await worker.fetch(post(payload({ verdicts: nested })), env());
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /nested too deeply/);
});

test("accepts the real summary shape, four levels deep", async () => {
  const real = payload({
    integration_version: "2026.10.10",
    transport: "modbus",
    device: { manufacturer: "Qvantum", model: null, firmware: "2026.10.10", hardware: null },
    plant: { sensor_mode: 4, room_comp_factor: 5.0, max_heating_supply: 60 },
    climate: { outdoor_min_c: 3.4, outdoor_max_c: 14.1, heating_hours: 148 },
    curve: {
      active: true,
      room_gain: {
        configured_c_per_c: 5.0,
        applied_c_per_c: 5.0,
        fitted: { c_per_c: 4.33, valid: true, hours: 66, std_err_c_per_c: 0.8 },
      },
    },
    counters: { since: "2026-10-12", reverts: 2, trim_updates: 6, days_clamped: 3 },
    fitted: { heat_loss_w_per_k: 97.6, scop_total: 2.7 },
    verdicts: {},
  });

  const response = await worker.fetch(post(real), env());
  assert.equal(response.status, 200);
});
