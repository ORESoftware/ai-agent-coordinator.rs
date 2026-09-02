import assert from "node:assert/strict";
import test from "node:test";
import { bucketKey, buildRequest, enqueue } from "../src/index.js";

const baseEnv = {
  ACTIVATION_MODE: "enabled",
  COORDINATOR_URL: "https://coordinator.example.test",
  AI_AGENT_COORDINATOR_API_TOKEN: "opaque-ci-value",
  LOOKBACK_HOURS: "1200",
  OVERLAP_HOURS: "6",
  MAX_WORKERS: "3"
};

test("same three-minute bucket converges on one key", () => {
  assert.equal(bucketKey(new Date("2026-09-02T17:01:01Z")), bucketKey(new Date("2026-09-02T17:02:59Z")));
  assert.notEqual(bucketKey(new Date("2026-09-02T17:02:59Z")), bucketKey(new Date("2026-09-02T17:03:00Z")));
});

test("payload encodes the 50-day window and hard worker cap", () => {
  const { payload } = buildRequest(new Date("2026-09-02T17:03:00Z"), baseEnv);
  assert.equal(payload.payload.source_contract.rolling_window_hours, 1200);
  assert.equal(payload.payload.source_contract.worker_concurrency_limit, 3);
  assert.equal(payload.payload.tracking.policy_path, "AGENTS.md");
  assert.equal(payload.payload.ledger_contract.skip_archived_cancelled_duplicate_superseded_or_outmoded, true);
});

test("four workers fail closed", () => {
  assert.throws(() => buildRequest(new Date(), { ...baseEnv, MAX_WORKERS: "4" }), /between 1 and 3/);
});

test("disabled scheduler performs no network request", async () => {
  let called = false;
  const result = await enqueue(new Date("2026-09-02T17:03:00Z"), { ...baseEnv, ACTIVATION_MODE: "disabled" }, async () => {
    called = true;
    throw new Error("unexpected");
  });
  assert.equal(result.status, "disabled");
  assert.equal(called, false);
});

test("enabled scheduler posts one redaction-safe request", async () => {
  let observed;
  const result = await enqueue(new Date("2026-09-02T17:03:00Z"), baseEnv, async (url, init) => {
    observed = { url: String(url), init };
    return new Response(JSON.stringify({ job: { id: "job-1" } }), { status: 201 });
  });
  assert.equal(result.status, "enqueued");
  assert.equal(observed.init.headers["idempotency-key"], result.run_key);
  assert.equal(JSON.parse(observed.init.body).payload.source_contract.rolling_window_hours, 1200);
  assert.equal(JSON.stringify(result).includes(baseEnv.AI_AGENT_COORDINATOR_API_TOKEN), false);
});
