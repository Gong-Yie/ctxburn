from ctxburn.capture import segments_from_anthropic, segments_from_openai
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
