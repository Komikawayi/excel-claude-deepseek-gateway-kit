# Claude Gateway Unified (English Notes)

This note explains two things:

1. How Claude-style inference works in this gateway.
2. Why token usage can become very expensive, and how to reduce it.

---

## 1. How Claude Works in This Gateway

This project keeps an Anthropic-compatible surface (`/v1/models`, `/v1/messages`) and forwards requests to DeepSeek, Kimi, MiMo, or MiniMax.

## MiniMax Notes (2026-05)

1. Supported in fixed mode: `ACTIVE_PROVIDER=minimax`.
2. Key prefixes:
   - `sk-api-*` -> PAYG
   - `sk-cp-*` -> Coding Plan / Token Plan
3. Region routing:
   - default via `MINIMAX_REGION=cn|global` (default `cn`)
   - per-request override via `x-minimax-region=cn|global`
4. Current `auto` mode is intentionally unchanged and does not auto-detect MiniMax prefixes.

### 1.1 Request lifecycle

1. The client sends a Claude-style request to `POST /v1/messages`.
2. The gateway sanitizes input:
   - validates message shape,
   - removes unsupported blocks,
   - normalizes tool definitions,
   - enforces size/depth limits.
3. The gateway maps model aliases (`opus`, `sonnet`, legacy `haiku`) to provider-specific models.
4. The gateway routes upstream by mode and key prefix:
   - fixed provider mode (`deepseek`, `kimi`, `mimo`), or
   - `auto` mode with prefix-based routing.
5. The upstream model returns either:
   - a final answer (`stop_reason=end_turn` / `max_tokens`), or
   - a tool call path (`tool_use` or pseudo XML tool-call patterns in some providers).
6. If auto web-search mode is enabled, the gateway can execute a local tool loop and send follow-up calls automatically (non-stream mode).

### 1.2 Tool loop semantics

In standard Claude client-tool semantics:

1. Assistant returns `tool_use`.
2. Client executes the tool.
3. Client sends `tool_result`.
4. Assistant returns the final answer.

This gateway implements a compatibility layer for unstable upstream behavior:

1. It can normalize pseudo XML tool calls into real `tool_use` blocks.
2. It can auto-inject `tool_result`.
3. It can force `tool_choice: none` after the first tool result to reduce endless re-calling.
4. It can fallback to a local aggregated final text if upstream keeps returning tool-call text.

---

## 2. Why Token Consumption Can Be Very High

The biggest downside in Claude-style multi-turn/tool workflows is token amplification.

### 2.1 Main reasons

1. Full-history resend:
   - Every follow-up request includes previous messages again.
   - Longer history means higher input token cost every round.

2. Tool loop expansion:
   - `tool_use` + `tool_result` adds extra structured content.
   - Web search results often include long snippets and URLs.

3. Thinking/reasoning carry-over:
   - Some providers require reasoning/thinking content to be passed back.
   - This can significantly increase input tokens in follow-up rounds.

4. Retry and multi-round loops:
   - Network retries + `AUTO_WEB_SEARCH_MAX_ROUNDS` can multiply requests.
   - Even when the final answer is short, intermediate rounds can be expensive.

5. Large outputs:
   - High `max_tokens` budgets increase worst-case output cost.
   - When not bounded, models may produce long verbose answers.

### 2.2 Practical impact

Token cost is not linear with “user prompt length.”  
It is often closer to:

`(history + tool payload + reasoning carry-over) x number_of_rounds`.

So a single user query can become several expensive upstream calls.

---

## 3. Cost-Control Recommendations

If your priority is stability + lower cost, use this checklist:

1. Keep `AUTO_WEB_SEARCH_MAX_ROUNDS` small (2-3).
2. Keep `AUTO_WEB_SEARCH_MAX_RESULTS` small (3-5).
3. Use moderate `max_tokens` for normal tasks.
4. Trim old conversation history in the client when possible.
5. Avoid unnecessary tool calls for simple prompts.
6. Prefer `sonnet` tier for routine tasks; reserve `opus` for hard tasks.
7. Use provider-specific coding model only when coding quality is truly needed.
8. Monitor timeout/retry rates; network instability directly increases cost.

---

## 4. Known Cost vs. Quality Tradeoff

1. Higher context and richer tool results can improve factual quality.
2. But they increase latency and token spend sharply.
3. In production, the best strategy is controlled depth:
   - strict token budgets,
   - minimal tool payloads,
   - bounded loop rounds,
   - selective use of premium models.

---

## 5. Suggested Positioning for Users

Use this gateway when you need:

1. Anthropic-compatible client integration.
2. Multi-provider routing behind one endpoint.
3. Strong compatibility handling for unstable tool-call behaviors.

Be aware that:

1. Token usage can be significantly higher than “single-call chat” systems.
2. Tool-rich workflows should be treated as a premium-cost mode, not a default mode.
