"""Incremental, read-only parser for Codex local JSONL metrics."""

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Optional


def compact_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _seconds(value: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, AttributeError):
        return None


@dataclass
class Metrics:
    turns: int = 0
    steps: int = 0
    tokens: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    context_tokens: int = 0
    context_cached_tokens: int = 0
    context_window: int = 0
    model_seconds: float = 0.0
    tool_seconds: float = 0.0
    tokens_per_second: float = 0.0
    last_update: Optional[float] = None

    @property
    def cache_percent(self) -> Optional[int]:
        if not self.input_tokens:
            return None
        return min(100, round(100 * self.cached_tokens / self.input_tokens))

    @property
    def context_percent(self) -> Optional[int]:
        if not self.context_window:
            return None
        return min(100, round(100 * self.context_tokens / self.context_window))

    @property
    def uncached_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_tokens)

    @property
    def context_remaining(self) -> int:
        return max(0, self.context_window - self.context_tokens)

    @property
    def average_tokens_per_second(self) -> Optional[float]:
        if self.model_seconds <= 0:
            return None
        return self.output_tokens / self.model_seconds


class SessionReader:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.offset = 0
        self.pending = b""
        self.metrics = Metrics()
        self.turn_ids = set()
        self.last_output_tokens = None
        self.last_token_time = None

    def refresh(self) -> Metrics:
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.__init__(self.path)
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read()
                self.offset = stream.tell()
        except OSError:
            return self.metrics

        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            self._consume(record)
        return self.metrics

    def _consume(self, record: dict) -> None:
        kind = record.get("type")
        payload = record.get("payload") or {}
        event = payload.get("type")
        stamp = _seconds(record.get("timestamp"))
        if kind == "turn_context":
            turn_id = payload.get("root_turn_id") or payload.get("turn_id")
            if turn_id:
                self.turn_ids.add(turn_id)
                self.metrics.turns = len(self.turn_ids)
        if kind != "event_msg":
            return
        if event == "item_completed":
            item = payload.get("item") or {}
            item_type = item.get("type")
            if item_type not in {"UserMessage", "UserMessageItem"}:
                self.metrics.steps += 1
                started = payload.get("started_at_ms")
                completed = payload.get("completed_at_ms")
                if isinstance(started, (int, float)) and isinstance(completed, (int, float)):
                    seconds = max(0.0, (completed - started) / 1000)
                    if item_type in {"Reasoning", "AgentMessage"}:
                        self.metrics.model_seconds += seconds
                    else:
                        self.metrics.tool_seconds += seconds
        elif event == "token_count":
            info = payload.get("info") or {}
            total = info.get("total_token_usage") or {}
            last = info.get("last_token_usage") or {}
            self.metrics.tokens = max(self.metrics.tokens, int(total.get("total_tokens") or 0))
            self.metrics.input_tokens = max(self.metrics.input_tokens, int(total.get("input_tokens") or 0))
            self.metrics.cached_tokens = max(self.metrics.cached_tokens, int(total.get("cached_input_tokens") or 0))
            self.metrics.output_tokens = max(self.metrics.output_tokens, int(total.get("output_tokens") or 0))
            self.metrics.context_tokens = int(last.get("input_tokens") or self.metrics.context_tokens)
            self.metrics.context_cached_tokens = int(last.get("cached_input_tokens") or self.metrics.context_cached_tokens)
            self.metrics.context_window = int(info.get("model_context_window") or self.metrics.context_window)
            output = int(total.get("output_tokens") or 0)
            if self.last_output_tokens is not None and self.last_token_time is not None and stamp:
                dt = stamp - self.last_token_time
                delta = output - self.last_output_tokens
                if 0.05 < dt < 120 and delta > 0:
                    current = delta / dt
                    self.metrics.tokens_per_second = (
                        current if not self.metrics.tokens_per_second else
                        0.65 * self.metrics.tokens_per_second + 0.35 * current
                    )
            self.last_output_tokens = output
            self.last_token_time = stamp
            self.metrics.last_update = stamp
        elif event == "task_complete":
            self.metrics.tokens_per_second = 0.0


def find_session(codex_home: Path, session_id: Optional[str] = None) -> Optional[Path]:
    root = Path(codex_home) / "sessions"
    if not root.is_dir():
        return None
    if session_id:
        matches = list(root.rglob(f"*{session_id}*.jsonl"))
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)
    # Only recent years/days are needed. mtime handles resumed conversations.
    files = list(root.rglob("*.jsonl"))
    return max(files, key=lambda p: p.stat().st_mtime) if files else None
