import assert from "node:assert";
import {
  canonicalJson,
  firstDivergence,
  foldSession,
  sortTools,
  sinkDynamicSystem,
  stabilize,
  prefixHash,
} from "../src/shared.js";

// canonicalJson: sorted keys
assert.equal(canonicalJson({ b: 1, a: 2 }), '{"a":2,"b":1}');

// firstDivergence
assert.equal(firstDivergence("abc", "abc"), -1);
assert.equal(firstDivergence("abcdef", "abcxef"), 3);

// sortTools stabilizes order
const p1 = sortTools({
  tools: [
    { type: "function", function: { name: "z_search" } },
    { type: "function", function: { name: "a_read" } },
  ],
});
assert.deepEqual(
  (p1.tools as Array<{ function: { name: string } }>).map((t) => t.function.name),
  ["a_read", "z_search"]
);

// sinkDynamic removes timestamp from system, appends to user
const p2 = sinkDynamicSystem({
  messages: [
    { role: "system", content: "You are helpful. timestamp=2026-08-29 trace_id=abc" },
    { role: "user", content: "hi" },
  ],
});
const sys = (p2.messages as Array<{ role: string; content: string }>)[0].content;
const user = (p2.messages as Array<{ role: string; content: string }>)[1].content;
assert.ok(!sys.includes("timestamp="), "system should not contain timestamp");
assert.ok(user.includes("[meta]"), "user should carry sunk meta");

// two payloads with different timestamps share the same stable prefix hash
const mk = (seed: string) => ({
  model: "deepseek-chat",
  messages: [
    { role: "system", content: `You are helpful. timestamp=${seed} trace_id=${seed}` },
    { role: "user", content: "hi" },
  ],
});
assert.equal(prefixHash(stabilize(mk("t1"))), prefixHash(stabilize(mk("t2"))));

// foldSession: usage buckets + divergence diagnostics
const stats = foldSession([
  { usage: { uncachedInputTokens: 900, cacheReadTokens: 100, cacheWriteTokens: 0 } },
  { usage: { uncachedInputTokens: 100, cacheReadTokens: 900, cacheWriteTokens: 0 } },
]);
assert.equal(stats.requests, 2);
assert.equal(stats.hitTokens, 1000);
assert.equal(stats.missTokens, 1000);
assert.equal(stats.hitRatio, 0.5);
assert.equal(stats.prefixDivergences, 0); // no payloads recorded

console.log("dsh-cache-guard pure checks: all passed");
