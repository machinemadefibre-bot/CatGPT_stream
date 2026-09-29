"""Live ChatGPT backend SSE capture helpers.

The browser UI already receives ChatGPT answers as an SSE response from
/backend-api/conversation.  This module mirrors that stream without altering
the response consumed by ChatGPT itself, then parses only user-facing final
assistant text.

The JavaScript tee intentionally writes to an in-page queue.  Python drains the
queue from the same Playwright page, avoiding cross-world exposeFunction quirks
in patched browser drivers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


LIVE_BACKEND_TEE_SCRIPT = r"""
(() => {
  const FLAG = "__catgptBackendStreamTeeV1";
  if (window[FLAG]) return;
  window[FLAG] = true;

  if (!Array.isArray(window.__catgptLiveSseQueue)) {
    window.__catgptLiveSseQueue = [];
  }
  window.__catgptLiveSseQueueBytes = 0;

  const MAX_ENTRIES = 4096;
  const MAX_BYTES = 4 * 1024 * 1024;

  const pushChunk = (streamId, chunk, done = false) => {
    try {
      const queue = window.__catgptLiveSseQueue;
      if (!Array.isArray(queue)) return;

      const text = typeof chunk === "string" ? chunk : "";
      if (
        queue.length >= MAX_ENTRIES ||
        window.__catgptLiveSseQueueBytes + text.length > MAX_BYTES
      ) {
        return;
      }

      window.__catgptLiveSseQueueBytes += text.length;
      queue.push([streamId, text, Boolean(done)]);
    } catch (_) {}
  };

  const originalFetch = window.fetch.bind(window);

  const isConversationPost = (input, init) => {
    try {
      const rawUrl =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input && typeof input.url === "string"
              ? input.url
              : "";

      const method = String(
        (init && init.method) ||
          (input && input.method) ||
          "GET"
      ).toUpperCase();

      if (method !== "POST") return false;

      const url = new URL(rawUrl, location.origin);
      if (url.origin !== location.origin) return false;

      return /^\/backend-api\/(f\/)?conversation$/.test(url.pathname);
    } catch (_) {
      return false;
    }
  };

  window.fetch = async function catgptTeedFetch(input, init) {
    const response = await originalFetch(input, init);

    try {
      if (!isConversationPost(input, init)) return response;

      const contentType = response.headers.get("content-type") || "";
      if (
        !contentType.includes("text/event-stream") ||
        !response.body ||
        response.bodyUsed
      ) {
        return response;
      }

      const copy = response.clone();
      if (!copy.body) return response;

      const reader = copy.body.getReader();
      const decoder = new TextDecoder();
      const streamId =
        "catgpt-" +
        Date.now().toString(36) +
        "-" +
        Math.random().toString(36).slice(2);

      (async () => {
        try {
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            pushChunk(streamId, decoder.decode(value, { stream: true }), false);
          }

          const tail = decoder.decode();
          if (tail) pushChunk(streamId, tail, false);
          pushChunk(streamId, "", true);
        } catch (_) {
          pushChunk(streamId, "", true);
        }
      })();

      return response;
    } catch (_) {
      return response;
    }
  };
})();
"""


DRAIN_BACKEND_QUEUE_SCRIPT = r"""
() => {
  const queue = Array.isArray(window.__catgptLiveSseQueue)
    ? window.__catgptLiveSseQueue
    : [];
  window.__catgptLiveSseQueue = [];
  window.__catgptLiveSseQueueBytes = 0;
  return queue;
}
"""


def message_text(message: dict[str, Any] | None) -> str:
    """Extract visible text from a ChatGPT backend message."""
    if not isinstance(message, dict):
        return ""

    content = message.get("content")
    if not isinstance(content, dict):
        return ""

    parts = content.get("parts")
    if isinstance(parts, list):
        output: list[str] = []
        for part in parts:
            if isinstance(part, str):
                output.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                output.append(part["text"])
        return "\n".join(part for part in output if part)

    text = content.get("text")
    return text if isinstance(text, str) else ""


def is_final_assistant_message(message: dict[str, Any] | None) -> bool:
    """Return True only for the user-facing final assistant answer."""
    if not isinstance(message, dict):
        return False

    author = message.get("author")
    if not isinstance(author, dict) or author.get("role") != "assistant":
        return False

    recipient = message.get("recipient")
    if recipient not in (None, "", "all"):
        return False

    content = message.get("content")
    if not isinstance(content, dict) or content.get("content_type") != "text":
        return False

    channel = message.get("channel")
    if channel not in (None, "", "final"):
        return False

    return True


def is_message_complete(message: dict[str, Any] | None) -> bool:
    if not isinstance(message, dict):
        return False

    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return bool(
        message.get("status") == "finished_successfully"
        or message.get("end_turn") is True
        or metadata.get("is_complete") is True
    )


@dataclass(slots=True)
class BackendSSEAccumulator:
    """Incrementally parse ChatGPT backend conversation SSE chunks."""

    conversation_id: str | None = None
    text: str = ""
    complete: bool = False
    _line_buffer: str = ""
    _data_lines: list[str] = field(default_factory=list)
    _tracking: bool = False

    def feed(self, chunk: str) -> bool:
        """Feed raw SSE bytes decoded as text. Returns True when text changed."""
        if not isinstance(chunk, str) or not chunk:
            return False

        before = self.text
        self._line_buffer += chunk

        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            self._handle_line(line.rstrip("\r"))

        return self.text != before

    def end(self) -> bool:
        before = self.text

        if self._line_buffer:
            self._handle_line(self._line_buffer.rstrip("\r"))
            self._line_buffer = ""

        self._flush_event()
        return self.text != before

    def _handle_line(self, line: str) -> None:
        if line == "":
            self._flush_event()
            return

        if line.startswith("data:"):
            self._data_lines.append(re.sub(r"^ ?", "", line[5:], count=1))

    def _flush_event(self) -> None:
        if not self._data_lines:
            return

        payload = "\n".join(self._data_lines)
        self._data_lines = []

        if payload == "[DONE]":
            self.complete = True
            return

        try:
            obj = json.loads(payload)
        except Exception:
            return

        self.handle_event_object(obj)

    def handle_event_object(self, obj: Any) -> None:
        if not isinstance(obj, dict):
            return

        if obj.get("type") == "message_stream_complete":
            self.complete = True
            conversation_id = obj.get("conversation_id")
            if isinstance(conversation_id, str):
                self.conversation_id = conversation_id
            return

        message = obj.get("message")
        if isinstance(message, dict):
            self._handle_message(message, obj.get("conversation_id"))
            return

        operation = obj.get("o")
        path = obj.get("p")
        value = obj.get("v")

        if operation in ("add", None) and isinstance(value, dict):
            nested_message = value.get("message")
            if isinstance(nested_message, dict):
                self._handle_message(
                    nested_message,
                    value.get("conversation_id") or obj.get("conversation_id"),
                )
                return

        if isinstance(value, list) and operation in ("patch", None):
            for entry in value:
                self._handle_patch_entry(entry)
            return

        if isinstance(value, str) and operation in ("append", None):
            if path is None or (
                isinstance(path, str)
                and re.fullmatch(r"/message/content/parts/\d+", path)
            ):
                if self._tracking:
                    self.text += value

    def _handle_patch_entry(self, entry: Any) -> None:
        if not isinstance(entry, dict):
            return

        path = entry.get("p")
        operation = entry.get("o")
        value = entry.get("v")

        if not isinstance(path, str):
            return

        if (
            re.fullmatch(r"/message/content/parts/\d+", path)
            and operation == "append"
            and isinstance(value, str)
        ):
            if self._tracking:
                self.text += value
            return

        if path == "/message/status" and value == "finished_successfully":
            if self._tracking:
                self.complete = True
            return

        if path == "/message/end_turn" and value is True and self._tracking:
            self.complete = True

    def _handle_message(self, message: dict[str, Any], conversation_id: Any) -> None:
        if isinstance(conversation_id, str):
            self.conversation_id = conversation_id

        if is_final_assistant_message(message):
            self._tracking = True
            text = message_text(message)
            if len(text) >= len(self.text):
                self.text = text
            if is_message_complete(message):
                self.complete = True
            return

        self._tracking = False
