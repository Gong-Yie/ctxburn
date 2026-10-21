"""Split each call's input tokens into fresh vs replayed context. Tokens only.

No prices live here on purpose. The provider reports how many tokens it billed,
so that is the number this tool reports -- dollar figures would need a price
table that goes stale and, worse, would be a guess dressed as a fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Segment, Trace
from .tokens import estimate_tokens

_PREVIEW = 110


@dataclass
class SegStat:
    kind: str
    key: str
    tokens: int
    cached_tokens: int
    replay: bool
    first_seen_at: int
    preview: str


@dataclass
class CallStat:
    index: int
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    fresh_tokens: int
    replay_tokens: int
    note: str | None = None
    segments: list[SegStat] = field(default_factory=list)


@dataclass
class Report:
    name: str
    calls: list[CallStat]
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    fresh_tokens: int
    replay_tokens: int
    by_kind: dict[str, list[int]]  # kind -> [tokens, replay_tokens, calls]
    top_segments: list[dict]  # heaviest single pieces of text, across the whole run
    notes: list[str]

    @property
    def replay_ratio(self) -> float:
        """Share of everything sent that had already been sent before."""
        return self.replay_tokens / self.total_input_tokens if self.total_input_tokens else 0.0

    @property
    def n_calls(self) -> int:
        return len(self.calls)

    @property
    def context_growth(self) -> float:
        """input tokens of the last call over the first. The O(n-1) multiplier."""
        if not self.calls:
            return 0.0
        return self.calls[-1].input_tokens / max(1, self.calls[0].input_tokens)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "fresh_tokens": self.fresh_tokens,
            "replay_tokens": self.replay_tokens,
            "replay_ratio": self.replay_ratio,
            "context_growth": self.context_growth,
            "n_calls": self.n_calls,
            "by_kind": self.by_kind,
            "top_segments": self.top_segments,
            "notes": self.notes,
            "calls": [
                {
                    "index": c.index,
                    "model": c.model,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "cached_tokens": c.cached_tokens,
                    "fresh_tokens": c.fresh_tokens,
                    "replay_tokens": c.replay_tokens,
                    "segments": [
                        {
                            "kind": s.kind,
                            "tokens": s.tokens,
                            "cached_tokens": s.cached_tokens,
                            "replay": s.replay,
                            "first_seen_at": s.first_seen_at,
                            "preview": s.preview,
                        }
                        for s in c.segments
                    ],
                }
                for c in self.calls
            ],
        }


def _preview(text: str) -> str:
    t = " ".join(text.split())
    return t[:_PREVIEW] + ("…" if len(t) > _PREVIEW else "")


def _split_tokens(segments: list[Segment], reported: int) -> list[int]:
    """Scale per-segment estimates so they sum to the provider-reported total."""
    est = [estimate_tokens(s.text) for s in segments]
    est_total = sum(est)
    if reported <= 0:
        return est
    if est_total <= 0:
        even = max(1, reported // max(1, len(segments)))
        return [even] * len(segments)
    scale = reported / est_total
    out = [round(e * scale) for e in est]
    drift = reported - sum(out)
    if out:
        out[-1] = max(0, out[-1] + drift)
    return out


def analyze(trace: Trace) -> Report:
    seen: dict[str, int] = {}
    calls: list[CallStat] = []
    by_kind: dict[str, list[int]] = {}
    agg: dict[str, dict] = {}

    for idx, call in enumerate(trace.calls):
        toks = _split_tokens(call.segments, call.input_tokens)
        replay_idx = [i for i, s in enumerate(call.segments) if s.key in seen]
        replay_total = sum(toks[i] for i in replay_idx)
        cached = max(0, min(call.cached_tokens, replay_total))

        # spread the provider's cached prefix across the replayed segments by weight
        seg_cached = [0] * len(call.segments)
        if cached and replay_total:
            left = cached
            for n, i in enumerate(replay_idx):
                if n == len(replay_idx) - 1:
                    seg_cached[i] = left
                else:
                    share = min(round(cached * toks[i] / replay_total), left)
                    seg_cached[i] = share
                    left -= share

        segs: list[SegStat] = []
        fresh_tokens = replay_tokens = 0
        for i, seg in enumerate(call.segments):
            t = toks[i]
            is_replay = seg.key in seen
            first = seen.setdefault(seg.key, idx)
            if is_replay:
                replay_tokens += t
            else:
                fresh_tokens += t
            bucket = by_kind.setdefault(seg.kind, [0, 0, 0])
            bucket[0] += t
            bucket[1] += t if is_replay else 0
            bucket[2] += 1
            a = agg.setdefault(
                seg.key,
                {
                    "kind": seg.kind,
                    "tokens": 0,
                    "sends": 0,
                    "replays": 0,
                    "preview": _preview(seg.text),
                    "first_seen_at": first,
                },
            )
            a["tokens"] += t
            a["sends"] += 1
            a["replays"] += 1 if is_replay else 0
            segs.append(
                SegStat(
                    kind=seg.kind,
                    key=seg.key,
                    tokens=t,
                    cached_tokens=seg_cached[i],
                    replay=is_replay,
                    first_seen_at=first,
                    preview=_preview(seg.text),
                )
            )

        calls.append(
            CallStat(
                index=idx,
                model=call.model,
                input_tokens=call.input_tokens or sum(toks),
                output_tokens=call.output_tokens,
                cached_tokens=cached,
                fresh_tokens=fresh_tokens,
                replay_tokens=replay_tokens,
                note=call.note,
                segments=segs,
            )
        )

    return Report(
        name=trace.name,
        calls=calls,
        total_input_tokens=sum(c.input_tokens for c in calls),
        total_output_tokens=sum(c.output_tokens for c in calls),
        total_cached_tokens=sum(c.cached_tokens for c in calls),
        fresh_tokens=sum(c.fresh_tokens for c in calls),
        replay_tokens=sum(c.replay_tokens for c in calls),
        by_kind=by_kind,
        top_segments=sorted(agg.values(), key=lambda a: -a["tokens"])[:8],
        notes=sorted({c.note for c in calls if c.note}),
    )
