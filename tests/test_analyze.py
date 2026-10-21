from ctxburn.analyze import analyze
from ctxburn.model import Call, Segment, Trace
from ctxburn.tokens import estimate_tokens

SYSTEM = "You are a careful coding agent. " * 40


def _trace(n=4, tool_tokens=300, model="claude-sonnet-4"):
    t = Trace(name="t")
    convo = [Segment("user", "do the thing")]
    for _ in range(n):
        segs = [Segment("system", SYSTEM), Segment("tools", "x" * tool_tokens), *convo]
        tok = sum(estimate_tokens(s.text) for s in segs)
        t.add(Call(model=model, segments=segs, input_tokens=tok, output_tokens=100))
        convo.append(Segment("assistant", f"step {len(convo)}"))
        convo.append(Segment("tool", f"result {len(convo)}"))
    return t


def test_first_call_is_all_fresh():
    r = analyze(_trace(1))
    assert r.replay_tokens == 0
    assert r.fresh_tokens > 0
    assert r.replay_ratio == 0


def test_system_prompt_is_replay_after_turn_one():
    r = analyze(_trace(4))
    first = r.calls[0]
    assert all(not s.replay for s in first.segments)
    later = r.calls[3]
    sys_seg = next(s for s in later.segments if s.kind == "system")
    assert sys_seg.replay and sys_seg.first_seen_at == 0


def test_replay_ratio_grows_with_turns():
    r = analyze(_trace(8))
    assert r.replay_ratio > 0.5
    assert r.calls[-1].replay_tokens > r.calls[1].replay_tokens


def test_segment_tokens_sum_to_reported():
    t = _trace(3)
    r = analyze(t)
    for call, rp in zip(t.calls, r.calls, strict=True):
        assert sum(s.tokens for s in rp.segments) == call.input_tokens


def test_fresh_plus_replay_equals_input_total():
    r = analyze(_trace(5))
    assert r.fresh_tokens + r.replay_tokens == r.total_input_tokens
    assert sum(c.fresh_tokens + c.replay_tokens for c in r.calls) == r.total_input_tokens


def test_cached_tokens_never_exceed_replay():
    t = _trace(3)
    t.calls[2].cached_tokens = 10_000_000
    r = analyze(t)
    c = r.calls[2]
    assert c.cached_tokens <= c.replay_tokens
    assert all(s.cached_tokens == 0 for s in c.segments if not s.replay)


def test_cached_does_not_change_the_ratio():
    """Caching is a billing discount, not a different amount of context."""
    t1 = _trace(3)
    t2 = _trace(3)
    t2.calls[2].cached_tokens = t2.calls[2].input_tokens
    assert analyze(t1).replay_ratio == analyze(t2).replay_ratio


def test_by_kind_totals_match_input():
    r = analyze(_trace(4))
    assert sum(v[0] for v in r.by_kind.values()) == r.total_input_tokens
    assert sum(v[1] for v in r.by_kind.values()) == r.replay_tokens


def test_top_segments_sorted_by_tokens():
    r = analyze(_trace(4))
    toks = [s["tokens"] for s in r.top_segments]
    assert toks == sorted(toks, reverse=True)
    assert r.top_segments[0]["kind"] == "system"


def test_output_tokens_tracked_separately():
    r = analyze(_trace(2))
    assert r.total_output_tokens == 200
    assert r.total_output_tokens not in (r.fresh_tokens, r.replay_tokens)


def test_empty_context_does_not_crash():
    t = Trace()
    t.add(Call(model="gpt-4o", segments=[], input_tokens=0, output_tokens=0))
    r = analyze(t)
    assert r.n_calls == 1
    assert r.total_input_tokens == 0
    assert r.replay_ratio == 0


def test_growth_metric():
    assert analyze(_trace(5)).context_growth > 1.0


def test_notes_carried_through():
    t = _trace(2)
    t.calls[1].note = "server-side context"
    r = analyze(t)
    assert r.notes == ["server-side context"]
