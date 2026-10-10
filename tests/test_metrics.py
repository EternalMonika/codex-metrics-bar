import json
from pathlib import Path
import tempfile
import unittest

from metrics import SessionReader, compact_tokens


def row(kind, payload, timestamp="2026-10-10T12:00:00Z"):
    return json.dumps({"type": kind, "payload": payload, "timestamp": timestamp}).encode() + b"\n"


class SessionReaderTests(unittest.TestCase):
    def test_incremental_metrics_and_partial_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            data = row("turn_context", {"turn_id": "a"})
            data += row("event_msg", {"type": "item_completed", "item": {"type": "UserMessage"}})
            data += row("event_msg", {"type": "item_completed", "item": {"type": "Reasoning"},
                                      "started_at_ms": 1000, "completed_at_ms": 3000})
            data += row("event_msg", {"type": "token_count", "info": {
                "total_token_usage": {"total_tokens": 1000, "input_tokens": 800,
                                      "cached_input_tokens": 400, "output_tokens": 200},
                "last_token_usage": {"input_tokens": 500, "cached_input_tokens": 300},
                "model_context_window": 2000,
            }})
            path.write_bytes(data + b'{"type":"event_msg"')
            reader = SessionReader(path)
            first = reader.refresh()
            self.assertEqual((first.turns, first.steps, first.tokens), (1, 1, 1000))
            self.assertEqual((first.cache_percent, first.context_percent), (50, 25))
            self.assertEqual((first.uncached_tokens, first.context_remaining), (400, 1500))
            self.assertEqual(first.average_tokens_per_second, 100)
            self.assertEqual(reader.refresh().steps, 1)
            with path.open("ab") as stream:
                stream.write(b'\n' + row("turn_context", {"turn_id": "b"}))
            self.assertEqual(reader.refresh().turns, 2)

    def test_token_units(self):
        self.assertEqual(compact_tokens(3_800_000), "3.8M")
        self.assertEqual(compact_tokens(12500), "12.5K")


if __name__ == "__main__":
    unittest.main()
