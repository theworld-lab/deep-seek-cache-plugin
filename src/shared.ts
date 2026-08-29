/**
 * Pure, dependency-free logic shared by the dsh-cache-guard host plugin.
 * Ported from the Python ds_cache_guard package (MIT).
 */
import { createHash } from "node:crypto";

export interface ChatMessage {
  role: string;
  content?: unknown;
  [k: string]: unknown;
}

export interface RequestPayload {
  model?: string;
  messages?: ChatMessage[];
  tools?: unknown[];
  [k: string]: unknown;
}

/** Deterministic serialization: sorted keys, fixed separators. */
export function canonicalJson(obj: unknown): string {
  return JSON.stringify(sortValue(obj));
}

function sortValue(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sortValue);
  if (v && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(v as Record<string, unknown>).sort()) {
      out[k] = sortValue((v as Record<string, unknown>)[k]);
    }
    return out;
  }
  return v;
}

/** Sort tools by function.name so tool-list serialization order is stable. */
export function sortTools(payload: RequestPayload): RequestPayload {
  if (Array.isArray(payload.tools)) {
    payload = {
      ...payload,
      tools: [...payload.tools].sort((a, b) => {
        const na = toolName(a);
        const nb = toolName(b);
        return na < nb ? -1 : na > nb ? 1 : 0;
      }),
    };
  }
  return payload;
}

function toolName(t: unknown): string {
  if (t && typeof t === "object") {
    const fn = (t as Record<string, unknown>)["function"];
    if (fn && typeof fn === "object") {
      const n = (fn as Record<string, unknown>)["name"];
      if (typeof n === "string") return n;
    }
  }
  return "";
}

const DYNAMIC_KEY =
  /(timestamp|trace[_-]?id|request[_-]?id|nonce|uuid)[A-Za-z_ -]*\s*[:=]\s*\S+/i;

/**
 * Move dynamic key=value fragments (timestamp=..., trace_id: ...) out of the
 * system prompt and re-append them to the last user message, preserving the
 * stable system prefix for cache hits.
 */
export function sinkDynamicSystem(payload: RequestPayload): RequestPayload {
  const messages = payload.messages;
  if (!Array.isArray(messages) || messages.length === 0) return payload;
  const extracted: string[] = [];
  const newMessages = messages.map((m) => {
    if (m && typeof m === "object" && (m as ChatMessage).role === "system") {
      const content = (m as ChatMessage).content;
      if (typeof content === "string") {
        const cleaned = content.replace(new RegExp(DYNAMIC_KEY.source, "gi"), (s) => {
          extracted.push(s.trim());
          return "";
        });
        return { ...(m as ChatMessage), content: cleaned.replace(/\s+/g, " ").trim() };
      }
    }
    return m;
  });
  if (extracted.length > 0) {
    for (let i = newMessages.length - 1; i >= 0; i--) {
      const m = newMessages[i] as ChatMessage;
      if (m.role === "user") {
        newMessages[i] = {
          ...m,
          content: `${typeof m.content === "string" ? m.content : ""}${
            m.content ? "\n" : ""
          }[meta] ${extracted.join(" | ")}`,
        };
        break;
      }
    }
  }
  return { ...payload, messages: newMessages };
}

/** Byte offset of the first difference between two strings (-1 if equal). */
export function firstDivergence(a: string, b: string): number {
  if (a === b) return -1;
  let lo = 0;
  let hi = Math.min(a.length, b.length);
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (a.slice(0, mid) === b.slice(0, mid)) lo = mid;
    else hi = mid - 1;
  }
  if (lo === Math.min(a.length, b.length) && a.length === b.length) return -1;
  return lo;
}

/** Canonical prefix string used for cache-friendliness comparison. */
export function prefixString(payload: RequestPayload): string {
  return canonicalJson({
    model: payload.model ?? "",
    messages: (payload.messages ?? []).filter(
      (m) => m && typeof m === "object" && (m as ChatMessage).role === "system"
    ),
    tools: payload.tools ?? [],
  });
}

export function prefixHash(payload: RequestPayload): string {
  return createHash("sha256").update(prefixString(payload)).digest("hex").slice(0, 16);
}

export interface CacheGuardOptions {
  sortTools?: boolean;
  sinkDynamic?: boolean;
}

/** Stabilize a request payload for prefix-cache friendliness. */
export function stabilize(payload: RequestPayload, opts: CacheGuardOptions = {}): RequestPayload {
  let p = payload;
  if (opts.sortTools !== false) p = sortTools(p);
  if (opts.sinkDynamic !== false) p = sinkDynamicSystem(p);
  return p;
}

export interface TurnRecord {
  time?: number | string;
  usage?: {
    uncachedInputTokens?: number;
    cacheReadTokens?: number;
    cacheWriteTokens?: number;
    outputTokens?: number;
    [k: string]: unknown;
  };
  payload?: RequestPayload;
}

export interface GuardStats {
  requests: number;
  prefixDivergences: number;
  hitRatio: number | null;
  hitTokens: number;
  missTokens: number;
  writeTokens: number;
  diagnostics: Array<{ offset: number; context: string }>;
}

/** Fold session turn logs into cache-guard statistics (pure). */
export function foldSession(turns: TurnRecord[]): GuardStats {
  let requests = 0;
  let hit = 0;
  let miss = 0;
  let write = 0;
  let lastPrefix = "";
  let first = true;
  const diagnostics: GuardStats["diagnostics"] = [];
  for (const t of turns) {
    requests += 1;
    const u = t.usage ?? {};
    hit += u.cacheReadTokens ?? 0;
    miss += u.uncachedInputTokens ?? 0;
    write += u.cacheWriteTokens ?? 0;
    if (t.payload) {
      const p = prefixString(t.payload);
      if (!first) {
        const off = firstDivergence(lastPrefix, p);
        if (off >= 0) {
          diagnostics.push({
            offset: off,
            context: p.slice(Math.max(0, off - 40), off + 40),
          });
        }
      }
      first = false;
      lastPrefix = p;
    }
  }
  const total = hit + miss;
  return {
    requests,
    prefixDivergences: diagnostics.length,
    hitRatio: total > 0 ? Math.round((hit / total) * 10000) / 10000 : null,
    hitTokens: hit,
    missTokens: miss,
    writeTokens: write,
    diagnostics: diagnostics.slice(-5),
  };
}
