# DeepSeek Harness Cache Reducer Plugin

一个面向 DeepSeek API / Agent Harness 的上下文缓存命中率优化插件。

## 它解决什么问题

DeepSeek 的 Context Caching 是**前缀匹配**机制：只有当本次请求与上次请求在开头
（system → tools → messages）逐字节一致时，前缀部分才能命中缓存（约 1/10 价格）。
Harness 场景下最常见的缓存失效原因：

1. **System prompt 里拼接了时间戳 / 随机数 / 动态环境信息** —— 每次请求前缀都变，100% miss。
2. **工具列表（tools）顺序不稳定** —— JSON 序列化顺序抖动导致前缀分叉。
3. **多轮对话中间插入/删除消息**（如注入新的记忆、RAG 片段到 system）—— 破坏公共前缀。
4. **field 顺序抖动 / 空白差异** —— 序列化不确定。

## 插件做了什么

`ds_cache_guard` 是一个可嵌入 Python harness 的请求前处理中间件：

| 功能 | 说明 |
|------|------|
| 前缀稳定性检查 | 对比上一请求，定位第一个字节级分叉点，打印 miss 诊断 |
| 动态字段下沉 | 自动把 `timestamp`、`trace_id`、`session_meta` 等动态字段从 system 头部挪到末尾（user 尾注） |
| 工具排序规范化 | 按 `function.name` 对 tools 排序，保证跨请求一致 |
| JSON 规范序列化 | `sort_keys` + 固定 separators，消除 field 顺序抖动 |
| 命中率统计 | 本地记录 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，输出命中率报表 |

## 快速开始

```bash
pip install -e .
```

```python
from ds_cache_guard import CacheGuard, wrap_client

guard = CacheGuard()
client = wrap_client(your_openai_compatible_client, guard=guard,
                     base_url="https://api.deepseek.com")

# 之后正常调用，guard 会自动做前缀稳定化
resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "system", "content": SYSTEM_PROMPT},
              {"role": "user", "content": user_input}],
)

print(guard.report())
# {'requests': 12, 'hit_ratio': 0.87, 'saved_tokens': 45210, ...}
```

或作为独立 CLI 诊断两个请求文件的前缀分叉：

```bash
ds-cache-guard diff req1.json req2.json
```

## 配置

环境变量或构造参数：

- `DS_CACHE_GUARD_STRICT=1`：发现前缀分叉时抛异常（用于 CI 回归测试）
- `DS_CACHE_GUARD_SINK_DYNAMIC=1`（默认开）：动态字段下沉
- `DS_CACHE_GUARD_SORT_TOOLS=1`（默认开）：工具排序

## 兼容性

任何 OpenAI 兼容客户端均可（openai>=1.0 python sdk）。插件不改写业务语义，
只重排/下沉对模型无语义影响但破坏前缀的元素。

## License

MIT
