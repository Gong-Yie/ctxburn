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
    assert r.cost_replay == 0
    assert r.cost_fresh > 0
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


def test_cached_tokens_never_exceed_replay():
    t = _trace(3)
    t.calls[2].cached_tokens = 10_000_000
    r = analyze(t)
    c = r.calls[2]
    assert c.cached_tokens <= c.replay_tokens
    assert all(s.cached_tokens == 0 for s in c.segments if not s.replay)


def test_cached_is_cheaper_than_uncached():
    t1 = _trace(3)
    t2 = _trace(3)
    t2.calls[2].cached_tokens = t2.calls[2].input_tokens
    a, b = analyze(t1), analyze(t2)
    assert b.cost_cached > 0
    assert b.total_cost < a.total_cost


def test_output_tokens_are_priced_separately():
    r = analyze(_trace(2))
    assert r.cost_output > 0
    assert abs(r.total_cost - (r.cost_fresh + r.cost_replay + r.cost_cached + r.cost_output)) < 1e-9


def test_unknown_model_still_prices():
    r = analyze(_trace(2, model="totally-made-up-9000"))
    assert r.total_cost > 0
    assert r.priced_estimate is True


def test_empty_context_does_not_crash():
    t = Trace()
    t.add(Call(model="gpt-4o", segments=[], input_tokens=0, output_tokens=0))
    r = analyze(t)
    assert r.n_calls == 1
    assert r.total_cost == 0


def test_growth_metric():
    r = analyze(_trace(5))
    assert r.context_growth > 1.0
