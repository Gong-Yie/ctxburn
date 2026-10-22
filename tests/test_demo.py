"""The demo trace must be importable from the installed package, not the repo."""

from ctxburn.demo_data import build


def test_demo_builds_in_both_languages():
    for lang in ("en", "zh"):
        trace = build(lang=lang)
        assert len(trace.calls) == 6
        assert trace.calls[-1].input_tokens > trace.calls[0].input_tokens
        assert trace.meta["lang"] == lang


def test_demo_has_no_network_or_key_requirement():
    trace = build()
    assert all(call.model for call in trace.calls)
    assert {seg.kind for call in trace.calls for seg in call.segments} >= {
        "system",
        "tools",
        "user",
        "assistant",
        "tool",
    }
