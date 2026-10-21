from ctxburn.capture import (
    segments_from_anthropic,
    segments_from_openai,
    segments_from_responses,
)
from ctxburn.model import Call, Segment, Trace
from ctxburn.tokens import estimate_tokens


def test_openai_system_tools_and_tool_calls():
    msgs = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
    ]
    segs = segments_from_openai(msgs, [{"type": "function", "function": {"name": "read"}}])
    kinds = [s.kind for s in segs]
    assert kinds == ["tools", "system", "user", "assistant", "tool"]
    assert "file body" in segs[-1].text
    assert "read" in segs[3].text  # empty content alone would collide with other calls


def test_openai_two_tool_calls_get_distinct_keys():
    a = segments_from_openai([{"role": "assistant", "content": None,
                               "tool_calls": [{"id": "1", "function": {"name": "a"}}]}], None)
    b = segments_from_openai([{"role": "assistant", "content": None,
                               "tool_calls": [{"id": "2", "function": {"name": "b"}}]}], None)
    assert a[0].key != b[0].key


def test_tools_split_per_tool():
    tools = [
        {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "b", "parameters": {"type": "object"}}},
    ]
    segs = segments_from_openai([{"role": "user", "content": "hi"}], tools)
    tool_segs = [s for s in segs if s.kind == "tools"]
    assert len(tool_segs) == 2
    assert tool_segs[0].key != tool_segs[1].key
    assert '"name": "a"' in tool_segs[0].text
    assert '"name": "b"' not in tool_segs[0].text


def test_same_tool_fingerprints_the_same_across_sdks():
    """A tool described identically must land on one segment, whichever SDK sent it."""
    a = segments_from_anthropic(
        [], None, [{"name": "read", "input_schema": {"type": "object"}}]
    )
    b = segments_from_openai(
        [],
        [{"type": "function", "function": {"name": "read", "input_schema": {"type": "object"}}}],
    )
    assert len(a) == len(b) == 1
    assert a[0].key == b[0].key


def test_anthropic_system_string_and_tool_result():
    segs = segments_from_anthropic(
        [
            {"role": "user", "content": "check the test"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "reading"},
                {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a.py"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "print(1)"},
            ]},
        ],
        "you are a bot",
        [{"name": "read_file", "input_schema": {"type": "object"}}],
    )
    kinds = [s.kind for s in segs]
    assert kinds == ["system", "tools", "user", "assistant", "assistant", "tool"]
    assert "print(1)" in segs[-1].text


def test_identical_context_produces_identical_keys():
    """The replay detector depends on this."""
    msgs = [{"role": "user", "content": "same"}]
    a = segments_from_openai(msgs, [{"z": 1, "a": 2}])[0]
    b = segments_from_openai(msgs, [{"a": 2, "z": 1}])[0]
    assert a.key == b.key


def test_responses_flat_input_items():
    segs = segments_from_responses(
        [
            {"role": "user", "content": "find the bug"},
            {"type": "function_call", "call_id": "c1", "name": "grep", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "found it"},
        ],
        "you are a bot",
        [{"type": "function", "name": "grep", "parameters": {"type": "object"}}],
    )
    assert [s.kind for s in segs] == ["system", "tools", "user", "assistant", "tool"]
    assert "found it" in segs[-1].text


def test_responses_string_input():
    segs = segments_from_responses("just a string", None, None)
    assert len(segs) == 1
    assert segs[0].kind == "user"


def test_responses_unknown_item_is_kept_not_dropped():
    segs = segments_from_responses([{"type": "reasoning", "summary": [{"text": "hm"}]}], None, None)
    assert len(segs) == 1
    assert "reasoning" in segs[0].text


def test_responses_two_calls_same_tool_get_distinct_keys():
    a = segments_from_responses(
        [{"type": "function_call", "call_id": "1", "name": "x", "arguments": "{}"}], None, None
    )
    b = segments_from_responses(
        [{"type": "function_call", "call_id": "2", "name": "x", "arguments": "{}"}], None, None
    )
    assert a[0].key != b[0].key


def test_watch_patches_responses_endpoint():
    import pytest

    pytest.importorskip("openai")
    from openai.resources.responses import Responses

    import ctxburn

    before = Responses.create
    ctxburn.watch(None)
    try:
        assert Responses.create is not before
    finally:
        ctxburn.unwatch()
    assert Responses.create is before


def test_trace_roundtrip(tmp_path):
    t = Trace(name="rt")
    t.add(Call(model="gpt-4o", segments=[Segment("user", "hello")],
               input_tokens=5, output_tokens=2))
    p = tmp_path / "t.jsonl"
    t.save(str(p))
    back = Trace.load(str(p))
    assert back.name == "rt"
    assert len(back.calls) == 1
    assert back.calls[0].segments[0].text == "hello"


def test_estimator_is_monotonic_and_cjk_aware():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)
    # one CJK char carries more information than one ASCII char
    assert estimate_tokens("字" * 100) > estimate_tokens("a" * 100)
