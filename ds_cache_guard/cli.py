import json
import sys

from . import CacheGuard, canonical_json, first_divergence


def main() -> int:
    if len(sys.argv) >= 4 and sys.argv[1] == "diff":
        a = json.load(open(sys.argv[2]))
        b = json.load(open(sys.argv[3]))
        sa, sb = canonical_json(a), canonical_json(b)
        off = first_divergence(sa, sb)
        if off < 0:
            print("✅ prefixes identical — cache-friendly")
            return 0
        print(f"⚠️ first divergence at byte {off}")
        print(f"  A: ...{sa[max(0, off - 40): off + 40]!r}")
        print(f"  B: ...{sb[max(0, off - 40): off + 40]!r}")
        return 1
    print("usage: ds-cache-guard diff <req1.json> <req2.json>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
