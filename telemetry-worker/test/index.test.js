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

const env = () => ({ QVANTUM_SUMMARIES: new FakeKV() });
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
