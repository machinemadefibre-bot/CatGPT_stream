from __future__ import annotations

import json
import unittest

from src.chatgpt.backend_stream import BackendSSEAccumulator


def sse(obj) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def final_message(text: str, *, status: str = "in_progress") -> dict:
    return {
        "author": {"role": "assistant"},
        "recipient": "all",
        "channel": "final",
        "content": {"content_type": "text", "parts": [text]},
        "status": status,
    }


class BackendSSEAccumulatorTests(unittest.TestCase):
    def test_legacy_full_message_replace_semantics(self) -> None:
        acc = BackendSSEAccumulator()
        self.assertTrue(
            acc.feed(
                sse(
                    {
                        "conversation_id": "conv-1",
                        "message": final_message("Hel"),
                    }
                )
            )
        )
        self.assertEqual(acc.text, "Hel")
        self.assertFalse(acc.complete)

        self.assertTrue(
            acc.feed(
                sse(
                    {
                        "conversation_id": "conv-1",
                        "message": final_message(
                            "Hello",
                            status="finished_successfully",
                        ),
                    }
                )
            )
        )
        self.assertEqual(acc.text, "Hello")
        self.assertTrue(acc.complete)
        self.assertEqual(acc.conversation_id, "conv-1")

    def test_delta_format_appends_final_text(self) -> None:
        acc = BackendSSEAccumulator()
        add = {
            "o": "add",
            "v": {
                "conversation_id": "conv-2",
                "message": final_message("Hel"),
            },
        }
        self.assertTrue(acc.feed(sse(add)))
        self.assertTrue(acc.feed(sse({"v": "lo"})))
        self.assertEqual(acc.text, "Hello")

        patch = {
            "o": "patch",
            "v": [
                {
                    "p": "/message/content/parts/0",
                    "o": "append",
                    "v": " world",
                },
                {
                    "p": "/message/status",
                    "o": "replace",
                    "v": "finished_successfully",
                },
            ],
        }
        self.assertTrue(acc.feed(sse(patch)))
        self.assertEqual(acc.text, "Hello world")
        self.assertTrue(acc.complete)
        self.assertEqual(acc.conversation_id, "conv-2")

    def test_non_final_messages_are_not_streamed(self) -> None:
        acc = BackendSSEAccumulator()
        reasoning = {
            "author": {"role": "assistant"},
            "recipient": "all",
            "channel": "analysis",
            "content": {"content_type": "text", "parts": ["secret reasoning"]},
            "status": "in_progress",
        }
        self.assertFalse(acc.feed(sse({"message": reasoning})))
        self.assertEqual(acc.text, "")

        self.assertTrue(acc.feed(sse({"message": final_message("Visible")})))
        self.assertEqual(acc.text, "Visible")

    def test_chunk_boundaries_can_split_sse_lines(self) -> None:
        acc = BackendSSEAccumulator()
        body = sse({"message": final_message("chunked")})
        midpoint = len(body) // 2
        self.assertFalse(acc.feed(body[:midpoint]))
        self.assertTrue(acc.feed(body[midpoint:]))
        self.assertEqual(acc.text, "chunked")

    def test_done_marker_marks_complete(self) -> None:
        acc = BackendSSEAccumulator()
        acc.feed(sse({"message": final_message("done")}))
        self.assertFalse(acc.complete)
        acc.feed("data: [DONE]\n\n")
        self.assertTrue(acc.complete)


if __name__ == "__main__":
    unittest.main()
