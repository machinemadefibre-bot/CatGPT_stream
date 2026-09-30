# CatGPT_stream

This fork tracks [TheBadFella/MimicGate](https://github.com/TheBadFella/MimicGate).  
This README only documents changes made in this fork; for the base project, installation, providers, configuration, and general API documentation, see the upstream repository.

## Changes in this fork

### Live ChatGPT streaming

ChatGPT `stream=true` requests now forward live user-facing output from ChatGPT's own `/backend-api/conversation` SSE stream instead of waiting for browser generation to finish and then replaying a completed response.

The browser page installs a request-scoped fetch tee that clones the backend conversation response without consuming or modifying the response used by the ChatGPT web app. The gateway incrementally extracts the growing final answer and converts it into append-only OpenAI streaming deltas.

This applies to OpenAI Chat Completions and the Responses API when the active provider is ChatGPT. Other providers keep the upstream completion-then-stream behavior.

### Responses API streaming

`/v1/responses` supports live SSE output for ChatGPT.

A streaming Responses request opens immediately, emits the normal response lifecycle events, sends a short `thinking...` reasoning-summary status while the model is still working, forwards live text deltas as they arrive, and periodically sends SSE keep-alive comments.

Completed tool calls are emitted using Responses-style `function_call` events after the structured payload has been validated. Stream execution errors are returned as `response.failed` instead of ending with a misleading successful completion.

### Responses tool-call compatibility

Responses requests accept both flat Responses function tools and the nested Chat Completions tool format.

The conversion layer also handles `function_call` and `function_call_output` input items, ignores replayed provider reasoning items, accepts previous assistant `output_text` content, and emits function-call output using `call_id` and the Responses `function_call` shape.

Provider built-ins such as `web_search` can be accepted for client compatibility, but they are not executed by the browser tool shim.

### Codex session continuity

Session routing now recognizes `thread-id`, `x-session-id`, and `session-id`, with `thread-id` taking precedence when present.

For Codex-style Responses requests using `store: false`, an explicit session header reuses the same browser tab/thread without creating a durable SQLite response-chain entry. Requests without an explicit reusable session remain stateless.

Header names are included in the internal session key so different identity mechanisms cannot accidentally collide.

### Large Codex/tool prompt externalization

For ChatGPT tool/function requests, large request prefixes can be moved out of the browser composer.

When the flattened prompt contains `Latest request to transform:` or `User prompt:`, everything before the last matching marker is written losslessly to a temporary UTF-8 Markdown attachment. The composer receives only a short instruction pointing to that attachment plus the actual latest request.

The temporary attachment is removed after the turn. This is intended to reduce the very large first-turn system/tool-schema payload commonly produced by Codex-style clients without discarding that context.

### Markdown long-prompt fallback

The generic ChatGPT long-prompt attachment fallback now writes temporary `.md` files instead of `.txt` files while preserving the prompt contents exactly.

### Browser-only proxy support

`BROWSER_PROXY_SERVER` can be supplied to route Playwright browser traffic through a proxy such as a SOCKS endpoint without proxying the whole container.

### Local Docker defaults

The Compose defaults in this fork differ from upstream:

- the default image is `catgpt-local:latest`;
- API port `8650` and browser GUI port `5800` bind to `127.0.0.1` by default;
- `API_TOKEN_OPTIONAL` defaults to `false`;
- the container includes `fonts-noto-cjk` for CJK rendering.

These defaults are intended for a locally built, privately exposed deployment rather than a directly public container service.
