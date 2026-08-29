from ds_cache_guard import CacheGuard, canonical_json, first_divergence


def make_payload(seed: str) -> dict:
    return {
        "model": "deepseek-chat",
        "tools": [
            {"type": "function", "function": {"name": "z_search", "parameters": {}}},
            {"type": "function", "function": {"name": "a_read", "parameters": {}}},
        ],
        "messages": [
            {"role": "system", "content": f"You are helpful. timestamp={seed} trace_id={seed}"},
            {"role": "user", "content": "hi"},
        ],
    }


def test_sort_tools_stabilizes_prefix():
    g = CacheGuard()
    p1 = g.process(make_payload("t1"))
    assert [t["function"]["name"] for t in p1["tools"]] == ["a_read", "z_search"]


def test_sink_dynamic_moves_timestamp_out_of_system():
    g = CacheGuard()
    p = g.process(make_payload("2026-08-29"))
    assert "timestamp=" not in p["messages"][0]["content"]


def test_two_requests_same_stable_prefix():
    g = CacheGuard()
    g.process(make_payload("t1"))
    g.process(make_payload("t2"))
    # dynamic fields sunk -> prefix hash unchanged
    assert g.report()["prefix_divergences"] == 0


def test_first_divergence():
    assert first_divergence("abc", "abc") == -1
    assert first_divergence("abcdef", "abcxef") == 3


def test_canonical_json_sorted():
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
