"""ds_cache_guard: reduce DeepSeek context-cache misses for agent harnesses.

DeepSeek context caching is a prefix match over the serialized request
(system -> tools -> messages). Any byte-level divergence in the prefix
causes a full cache miss. This plugin stabilizes request prefixes and
reports hit/miss statistics.

MIT License.
"""
from __future__ import annotations

import copy as _copy
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__version__ = "0.1.0"


def canonical_json(obj: Any) -> str:
    """Deterministic serialization: sorted keys, fixed separators."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sort_tools(payload: Dict[str, Any]) -> Dict[str, Any]:
    tools = payload.get("tools")
    if isinstance(tools, list):
        payload["tools"] = sorted(
            tools,
            key=lambda t: (
                (t.get("function", {}) or {}).get("name", "")
                if isinstance(t, dict)
                else str(t)
            ),
        )
    return payload


def _sink_dynamic_system(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Move dynamic key=value fragments out of the system prompt."""
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return payload
    extracted: List[str] = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "system":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue

        def _sub(m: "re.Match") -> str:
            extracted.append(m.group(0).strip())
            return ""

        content = re.sub(
            r"[A-Za-z_ -]*(?:timestamp|trace[_-]?id|request[_-]?id|nonce|uuid)[A-Za-z_ -]*\s*[:=]\s*\S+",
            _sub,
            content,
            flags=re.IGNORECASE,
        )
        msg["content"] = re.sub(r"\s+", " ", content).strip()
    if extracted and messages:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                tail = msg.get("content")
                msg["content"] = (tail or "") + ("\n" if tail else "") + "[meta] " + " | ".join(extracted)
                break
    return payload


def first_divergence(a: str, b: str) -> int:
    """UTF-8 byte offset of the first difference between two strings (-1 if equal)."""
    if a == b:
        return -1
    lo, hi = 0, min(len(a), len(b))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    if lo == min(len(a), len(b)) and len(a) == len(b):
        return -1
    return len(a[:lo].encode("utf-8"))


@dataclass
class CacheGuard:
    """Request pre-processor + cache-hit-rate tracker for DeepSeek harnesses."""

    sink_dynamic: bool = field(default_factory=lambda: os.environ.get("DS_CACHE_GUARD_SINK_DYNAMIC", "1") == "1")
    sort_tools: bool = field(default_factory=lambda: os.environ.get("DS_CACHE_GUARD_SORT_TOOLS", "1") == "1")
    strict: bool = field(default_factory=lambda: os.environ.get("DS_CACHE_GUARD_STRICT", "0") == "1")

    _requests: int = 0
    _hit_tokens: int = 0
    _miss_tokens: int = 0
    _last_prefix_hash: Optional[str] = None
    _last_prefix: str = ""
    _diagnostics: List[Dict[str, Any]] = field(default_factory=list)

    def process(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Stabilize a chat.completions request payload and track prefix.

        Returns a deep-copied payload; the caller's input is never mutated.
        """
        payload = _copy.deepcopy(payload)
        if self.sort_tools:
            payload = _sort_tools(payload)
        if self.sink_dynamic:
            payload = _sink_dynamic_system(payload)
        prefix = canonical_json(
            {
                "model": payload.get("model", ""),
                "messages": [m for m in payload.get("messages", []) if isinstance(m, dict) and m.get("role") == "system"],
                "tools": payload.get("tools", []),
            }
        )
        h = hashlib.sha256(prefix.encode()).hexdigest()[:16]
        self._requests += 1
        if self._last_prefix_hash is not None and h != self._last_prefix_hash:
            off = first_divergence(self._last_prefix, prefix)
            diag = {
                "request_no": self._requests,
                "divergence_offset": off,
                "context": prefix[max(0, off - 40): off + 40] if off >= 0 else "",
            }
            self._diagnostics.append(diag)
            if self.strict:
                raise RuntimeError(f"ds-cache-guard: prefix divergence at {off}: {diag['context']!r}")
        self._last_prefix_hash = h
        self._last_prefix = prefix
        return payload

    def observe_usage(self, usage: Optional[Dict[str, Any]]) -> None:
        """Feed prompt usage from a response to accumulate hit/miss tokens."""
        if not usage:
            return
        self._hit_tokens += int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        self._miss_tokens += int(usage.get("prompt_cache_miss_tokens", 0) or 0)

    def report(self) -> Dict[str, Any]:
        total = self._hit_tokens + self._miss_tokens
        return {
            "requests": self._requests,
            "prompt_cache_hit_tokens": self._hit_tokens,
            "prompt_cache_miss_tokens": self._miss_tokens,
            "hit_ratio": round(self._hit_tokens / total, 4) if total else None,
            "prefix_divergences": len(self._diagnostics),
            "diagnostics": self._diagnostics[-5:],
        }


def wrap_client(client: Any, guard: Optional[CacheGuard] = None) -> Any:
    """Wrap an OpenAI-compatible client so chat.completions.create goes through the guard."""
    g = guard or CacheGuard()

    class _GuardedCompletions:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def create(self, **kwargs: Any) -> Any:
            kwargs = g.process(kwargs)
            resp = self._inner.create(**kwargs)
            usage = getattr(resp, "usage", None)
            if usage is not None:
                if isinstance(usage, dict):
                    g.observe_usage(usage)
                else:
                    try:
                        g.observe_usage(vars(usage))
                    except TypeError:
                        pass
            return resp

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    class _GuardedChat:
        completions = _GuardedCompletions(client.chat.completions)

    class _GuardedClient:
        chat = _GuardedChat()

        def __getattr__(self, name: str) -> Any:
            return getattr(client, name)

    return _GuardedClient()
"""ds_cache_guard: reduce DeepSeek context-cache misses for agent harnesses.

DeepSeek context caching is a prefix match over the serialized request
(system -> tools -> messages). Any byte-level divergence in the prefix
causes a full cache miss. This plugin stabilizes request prefixes and
reports hit/miss statistics.

MIT License.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__version__ = "0.1.0"


def canonical_json(obj: Any) -> str:
    """Deterministic serialization: sorted keys, fixed separators."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sort_tools(payload: Dict[str, Any]) -> Dict[str, Any]:
    tools = payload.get("tools")
    if isinstance(tools, list):
        payload["tools"] = sorted(
            tools,
            key=lambda t: (
                (t.get("function", {}) or {}).get("name", "")
                if isinstance(t, dict)
                else str(t)
            ),
        )
    return payload


def _sink_dynamic_system(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Move dynamic key=value fragments out of the system prompt."""
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return payload
    extracted: List[str] = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "system":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue

        def _sub(m: "re.Match") -> str:
            extracted.append(m.group(0).strip())
            return ""

        content = re.sub(
            r"[A-Za-z_ -]*(?:timestamp|trace[_-]?id|request[_-]?id|nonce|uuid)[A-Za-z_ -]*\s*[:=]\s*\S+",
            _sub,
            content,
            flags=re.IGNORECASE,
        )
        msg["content"] = content.strip()
    if extracted and messages:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                tail = msg.get("content")
                msg["content"] = (tail or "") + ("\n" if tail else "") + "[meta] " + " | ".join(extracted)
                break
    return payload


def first_divergence(a: str, b: str) -> int:
    """Byte offset of the first difference between two strings (-1 if equal)."""
    if a == b:
        return -1
    lo, hi = 0, min(len(a), len(b))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    if lo == min(len(a), len(b)) and len(a) == len(b):
        return -1
    return lo


@dataclass
class CacheGuard:
    """Request pre-processor + cache-hit-rate tracker for DeepSeek harnesses."""

    sink_dynamic: bool = field(default_factory=lambda: os.environ.get("DS_CACHE_GUARD_SINK_DYNAMIC", "1") == "1")
    sort_tools: bool = field(default_factory=lambda: os.environ.get("DS_CACHE_GUARD_SORT_TOOLS", "1") == "1")
    strict: bool = field(default_factory=lambda: os.environ.get("DS_CACHE_GUARD_STRICT", "0") == "1")

    _requests: int = 0
    _hit_tokens: int = 0
    _miss_tokens: int = 0
    _last_prefix_hash: Optional[str] = None
    _last_prefix: str = ""
    _diagnostics: List[Dict[str, Any]] = field(default_factory=list)

    def process(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Stabilize a chat.completions request payload and track prefix."""
        payload = dict(payload)
        if self.sort_tools:
            payload = _sort_tools(payload)
        if self.sink_dynamic:
            payload = _sink_dynamic_system(payload)
        prefix = canonical_json(
            {
                "model": payload.get("model", ""),
                "messages": [m for m in payload.get("messages", []) if isinstance(m, dict) and m.get("role") == "system"],
                "tools": payload.get("tools", []),
            }
        )
        h = hashlib.sha256(prefix.encode()).hexdigest()[:16]
        self._requests += 1
        if self._last_prefix_hash is not None and h != self._last_prefix_hash:
            off = first_divergence(self._last_prefix, prefix)
            diag = {
                "request_no": self._requests,
                "divergence_offset": off,
                "context": prefix[max(0, off - 40): off + 40] if off >= 0 else "",
            }
            self._diagnostics.append(diag)
            if self.strict:
                raise RuntimeError(f"ds-cache-guard: prefix divergence at {off}: {diag['context']!r}")
        self._last_prefix_hash = h
        self._last_prefix = prefix
        return payload

    def observe_usage(self, usage: Optional[Dict[str, Any]]) -> None:
        """Feed prompt usage from a response to accumulate hit/miss tokens."""
        if not usage:
            return
        self._hit_tokens += int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        self._miss_tokens += int(usage.get("prompt_cache_miss_tokens", 0) or 0)

    def report(self) -> Dict[str, Any]:
        total = self._hit_tokens + self._miss_tokens
        return {
            "requests": self._requests,
            "prompt_cache_hit_tokens": self._hit_tokens,
            "prompt_cache_miss_tokens": self._miss_tokens,
            "hit_ratio": round(self._hit_tokens / total, 4) if total else None,
            "prefix_divergences": len(self._diagnostics),
            "diagnostics": self._diagnostics[-5:],
        }


def wrap_client(client: Any, guard: Optional[CacheGuard] = None) -> Any:
    """Wrap an OpenAI-compatible client so chat.completions.create goes through the guard."""
    g = guard or CacheGuard()

    class _GuardedCompletions:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def create(self, **kwargs: Any) -> Any:
            kwargs = g.process(kwargs)
            resp = self._inner.create(**kwargs)
            usage = getattr(resp, "usage", None)
            if usage is not None:
                if isinstance(usage, dict):
                    g.observe_usage(usage)
                else:
                    try:
                        g.observe_usage(vars(usage))
                    except TypeError:
                        pass
            return resp

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    class _GuardedChat:
        completions = _GuardedCompletions(client.chat.completions)

    class _GuardedClient:
        chat = _GuardedChat()

        def __getattr__(self, name: str) -> Any:
            return getattr(client, name)

    return _GuardedClient()
