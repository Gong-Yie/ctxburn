"""Core data model: a trace is a list of LLM calls, each carrying the full context."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field

KINDS = ("system", "tools", "user", "assistant", "tool")


def fingerprint(kind: str, text: str) -> str:
    """Stable content id. Same text in a later turn => same key => replay."""
    h = hashlib.blake2b(f"{kind}\x00{text}".encode(), digest_size=8)
    return h.hexdigest()


@dataclass
class Segment:
    """One piece of the context window: a system prompt, a tool schema, a message."""

    kind: str
    text: str
    key: str = ""
    tokens: int = 0

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown segment kind: {self.kind}")
        if not self.key:
            self.key = fingerprint(self.kind, self.text)


@dataclass
class Call:
    """A single LLM request. `segments` is the *entire* context sent with it."""

    model: str = "unknown"
    segments: list[Segment] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: float = 0.0
    ts: float = field(default_factory=time.time)
    error: str | None = None
    note: str | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> Call:
        segs = [Segment(**s) for s in d.pop("segments", [])]
        return cls(segments=segs, **d)


@dataclass
class Trace:
    name: str = "run"
    calls: list[Call] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def add(self, call: Call) -> Call:
        self.calls.append(call)
        return call

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"name": self.name, "meta": self.meta}) + "\n")
            for c in self.calls:
                f.write(json.dumps(c.to_json(), ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: str) -> Trace:
        calls: list[Call] = []
        meta: dict = {}
        name = "run"
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if i == 0 and "calls" not in d:
                    name = d.get("name", "run")
                    meta = d.get("meta", {})
                    continue
                calls.append(Call.from_json(d))
        return cls(name=name, calls=calls, meta=meta)
