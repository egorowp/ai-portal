"""Tests for inbox scheduling and duplicate suppression."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent import main as agent_main


class InboxTests(unittest.TestCase):
    def test_future_retry_item_is_queued_but_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            (inbox / "item.json").write_text(
                json.dumps({"url": "https://example.com/a", "retry_after": future}),
                encoding="utf-8",
            )
            with patch.object(agent_main, "INBOX", inbox), patch.dict(
                os.environ, {"INBOX_MAX_ATTEMPTS": "8"}, clear=False
            ):
                ready, queued = agent_main.load_inbox_items(set())
            self.assertEqual(ready, [])
            self.assertEqual(queued, {"https://example.com/a"})

    def test_due_item_is_ready_and_existing_news_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            old = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            (inbox / "retry.json").write_text(
                json.dumps({"url": "https://example.com/retry", "retry_after": old}),
                encoding="utf-8",
            )
            (inbox / "published.json").write_text(
                json.dumps({"url": "https://example.com/published"}),
                encoding="utf-8",
            )
            with patch.object(agent_main, "INBOX", inbox):
                ready, queued = agent_main.load_inbox_items({"https://example.com/published"})
            self.assertEqual([item["url"] for item in ready], ["https://example.com/retry"])
            self.assertEqual(queued, {"https://example.com/retry", "https://example.com/published"})

    def test_exhausted_item_remains_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            (inbox / "exhausted.json").write_text(
                json.dumps({"url": "https://example.com/exhausted", "generation_attempts": 8}),
                encoding="utf-8",
            )
            with patch.object(agent_main, "INBOX", inbox), patch.dict(
                os.environ, {"INBOX_MAX_ATTEMPTS": "8"}, clear=False
            ):
                ready, queued = agent_main.load_inbox_items(set())
            self.assertEqual(ready, [])
            self.assertEqual(queued, {"https://example.com/exhausted"})


if __name__ == "__main__":
    unittest.main()
