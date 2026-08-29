/**
 * dsh-cache-guard host plugin.
 *
 * Registered via cordis.patch.yml as `cache-guard`. Uses only third-party-safe,
 * structurally declared context services:
 *  - ctx.settings     → `cache-guard` namespace (sortTools / sinkDynamic toggles)
 *  - ctx.sessionProjections → `cache-guard` projection unit (pure fold of
 *    session logs: request payload prefixes + normalized usage buckets)
 *
 * The projection gives every session: request count, prefix divergence
 * diagnostics (byte offset + context) and cache hit ratio computed from
 * uncachedInputTokens / cacheReadTokens / cacheWriteTokens — the same
 * normalized buckets used by dsh-meter / dsh-cachescope.
 */
import { foldSession, prefixHash, prefixString, stabilize, type RequestPayload } from "../shared.js";

export interface SettingsLike {
  watch(ns: string, cb: (value: unknown) => void): void;
}

export interface ProjectionsLike {
  define(name: string, unit: (session: unknown) => unknown): void;
}

export interface GuardContext {
  settings?: SettingsLike;
  sessionProjections?: ProjectionsLike;
  sessions?: { list?: () => Iterable<unknown> };
}

export interface GuardConfig {
  sortTools: boolean;
  sinkDynamic: boolean;
}

const DEFAULT_CONFIG: GuardConfig = { sortTools: true, sinkDynamic: true };

function coerceConfig(value: unknown): GuardConfig {
  const v = (value ?? {}) as Record<string, unknown>;
  return {
    sortTools: v.sortTools !== false,
    sinkDynamic: v.sinkDynamic !== false,
  };
}

/** Extract request payload records from a session log (structural, tolerant). */
function extractTurns(session: unknown): Array<Record<string, any>> {
  const s = (session ?? {}) as Record<string, unknown>;
  const raw =
    (Array.isArray(s["turns"]) && s["turns"]) ||
    (Array.isArray(s["log"]) && s["log"]) ||
    (Array.isArray(s["requests"]) && s["requests"]) ||
    [];
  return raw as Array<Record<string, any>>;
}

function hashOf(payload: RequestPayload): string {
  return prefixHash(payload);
}

function prefixOf(payload: RequestPayload, config: GuardConfig): string {
  return prefixString(stabilize(payload, config));
}

export function apply(ctx: GuardContext, config: GuardConfig = DEFAULT_CONFIG): void {
  let current = config;

  if (ctx.settings) {
    ctx.settings.watch("cache-guard", (value) => {
      current = coerceConfig(value);
    });
  }

  if (ctx.sessionProjections) {
    ctx.sessionProjections.define("cache-guard", (session: unknown) => {
      const turns = extractTurns(session);
      const stats = foldSession(turns as any);
      return {
        config: current,
        stats,
        // Per-turn stabilization preview: what the request prefix would be
        // after applying the current toggles (diagnostic only).
        stabilized: turns.map((t) =>
          t.payload ? { hash: hashOf(t.payload), prefix: prefixOf(t.payload, current) } : null
        ),
      };
    });
  }
}

export const name = "dsh-cache-guard";
