# llm

Subscription-backed Claude Code client — shared LLM boundary.

`ClaudeCodeClient` is duck-typed like `anthropic.Anthropic()` (exposes
`.messages.create(...)`) but routes calls through `claude -p` headless mode so they
bill against the Max subscription's included usage instead of the separate
pay-per-token developer API. The response JSON schema is a required constructor
argument — each caller (e.g. `distill`, `brain`) supplies its own schema; there is no
default.
