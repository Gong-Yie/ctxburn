"""The whole point: split each call's input tokens into fresh vs replay, then price it."""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Segment, Trace
from .pricing import Price, lookup
from .tokens import estimate_tokens

_PREVIEW = 110


@dataclass
class SegCost:
    kind: str
    key: str
    tokens: int
    cached_tokens: int
    replay: bool
    first_seen_at: int
    cost: float
    preview: str


@dataclass
class CallCost:
    index: int
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    fresh_tokens: int
    replay_tokens: int
    cost_fresh: float
    cost_replay: float
    cost_cached: float
    cost_output: float
    cost: float
    segments: list[SegCost] = field(default_factory=list)


@dataclass
class Report:
    name: str
    calls: list[CallCost]
    total_cost: float
    cost_fresh: float
    cost_replay: float
    cost_cached: float
    cost_output: float
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    by_kind: dict[str, list[float]]  # kind -> [tokens, cost, replay_tokens]
    top_segments: list[dict]  # heaviest single pieces of text, across the whole run
    priced_estimate: bool

    @property
    def replay_ratio(self) -> float:
        """Share of every input dollar that paid for text already sent."""
        input_cost = self.cost_fresh + self.cost_replay + self.cost_cached
        return (self.cost_replay + self.cost_cached) / input_cost if input_cost else 0.0

    @property
    def n_calls(self) -> int:
        return len(self.calls)

    @property
    def context_growth(self) -> float:
        """input tokens of the last call over the first. The O(n-1) multiplier."""
        if not self.calls:
            return 0.0
        first = max(1, self.calls[0].input_tokens)
        return self.calls[-1].input_tokens / first

    @property
    def per_turn_cost(self) -> float:
        return self.total_cost / self.n_calls if self.n_calls else 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "total_cost": self.total_cost,
            "cost_fresh": self.cost_fresh,
            "cost_replay": self.cost_replay,
            "cost_cached": self.cost_cached,
            "cost_output": self.cost_output,
            "replay_ratio": self.replay_ratio,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "context_growth": self.context_growth,
            "n_calls": self.n_calls,
            "by_kind": self.by_kind,
            "top_segments": self.top_segments,
            "priced_estimate": self.priced_estimate,
            "calls": [
                {
                    "index": c.index,
                    "model": c.model,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "cached_tokens": c.cached_tokens,
                    "fresh_tokens": c.fresh_tokens,
                    "replay_tokens": c.replay_tokens,
                    "cost": c.cost,
                    "cost_fresh": c.cost_fresh,
                    "cost_replay": c.cost_replay,
                    "cost_cached": c.cost_cached,
                    "cost_output": c.cost_output,
                    "segments": [
                        {
                            "kind": s.kind,
                            "tokens": s.tokens,
                            "cached_tokens": s.cached_tokens,
                            "replay": s.replay,
                            "first_seen_at": s.first_seen_at,
                            "cost": s.cost,
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


def analyze(trace: Trace, overrides: dict[str, Price] | None = None) -> Report:
    overrides = overrides or {}
    seen: dict[str, int] = {}
    calls: list[CallCost] = []
    by_kind: dict[str, list[float]] = {}
    agg: dict[str, dict] = {}

    for idx, call in enumerate(trace.calls):
        price = overrides.get(call.model) or lookup(call.model)
        toks = _split_tokens(call.segments, call.input_tokens)
        replay_idx = [
            i for i, s in enumerate(call.segments) if s.key in seen
        ]
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
                    share = round(cached * toks[i] / replay_total)
                    share = min(share, left)
                    seg_cached[i] = share
                    left -= share

        segs: list[SegCost] = []
        cost_fresh = cost_replay = cost_cached = 0.0
        fresh_tokens = replay_tokens = 0
        for i, seg in enumerate(call.segments):
            t = toks[i]
            is_replay = seg.key in seen
            first = seen.setdefault(seg.key, idx)
            billable = t - seg_cached[i]
            if is_replay:
                c_bill = billable * price.input / 1e6
                c_cache = seg_cached[i] * price.cached / 1e6
                cost_replay += c_bill
                cost_cached += c_cache
                replay_tokens += t
                cost = c_bill + c_cache
            else:
                cost = t * price.input / 1e6
                cost_fresh += cost
                fresh_tokens += t
            bucket = by_kind.setdefault(seg.kind, [0.0, 0.0, 0.0])
            bucket[0] += t
            bucket[1] += cost
            bucket[2] += t if is_replay else 0
            a = agg.setdefault(
                seg.key,
                {
                    "kind": seg.kind,
                    "tokens": 0,
                    "cost": 0.0,
                    "sends": 0,
                    "replays": 0,
                    "preview": _preview(seg.text),
                    "first_seen_at": first,
                },
            )
            a["tokens"] += t
            a["cost"] += cost
            a["sends"] += 1
            a["replays"] += 1 if is_replay else 0
            segs.append(
                SegCost(
                    kind=seg.kind,
                    key=seg.key,
                    tokens=t,
                    cached_tokens=seg_cached[i],
                    replay=is_replay,
                    first_seen_at=first,
                    cost=cost,
                    preview=_preview(seg.text),
                )
            )

        cost_out = call.output_tokens * price.output / 1e6
        calls.append(
            CallCost(
                index=idx,
                model=call.model,
                input_tokens=call.input_tokens or sum(toks),
                output_tokens=call.output_tokens,
                cached_tokens=cached,
                fresh_tokens=fresh_tokens,
                replay_tokens=replay_tokens,
                cost_fresh=cost_fresh,
                cost_replay=cost_replay,
                cost_cached=cost_cached,
                cost_output=cost_out,
                cost=cost_fresh + cost_replay + cost_cached + cost_out,
                segments=segs,
            )
        )

    total = sum(c.cost for c in calls)
    return Report(
        name=trace.name,
        calls=calls,
        total_cost=total,
        cost_fresh=sum(c.cost_fresh for c in calls),
        cost_replay=sum(c.cost_replay for c in calls),
        cost_cached=sum(c.cost_cached for c in calls),
        cost_output=sum(c.cost_output for c in calls),
        total_input_tokens=sum(c.input_tokens for c in calls),
        total_output_tokens=sum(c.output_tokens for c in calls),
        total_cached_tokens=sum(c.cached_tokens for c in calls),
        by_kind=by_kind,
        top_segments=sorted(agg.values(), key=lambda a: -a["cost"])[:8],
        priced_estimate=any((overrides.get(c.model) or lookup(c.model)).estimated for c in calls),
    )
