"""Capture real calls by wrapping the OpenAI and Anthropic SDKs.

    import ctxburn
    ctxburn.watch("run.jsonl")        # before you build the client
    ... your agent runs unchanged ...
    # run.jsonl now has one line per LLM call, with the full context it sent

Nothing is sent anywhere; this only reads the request kwargs and the usage the
provider already returns. Streaming is supported for OpenAI when
`stream_options={"include_usage": True}` is set; otherwise streamed calls are
skipped with a warning rather than guessed at.
"""

from __future__ import annotations

import contextlib
import json
import sys
import warnings

from .model import Call, Segment, Trace

_ACTIVE: list[_Recorder] = []
_ORIGINALS: list[tuple[object, str, object]] = []


def _warn(msg: str) -> None:
    warnings.warn(f"ctxburn: {msg}", RuntimeWarning, stacklevel=3)


# ---------------------------------------------------------------- normalizing


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                if p.get("type") == "text":
                    parts.append(p.get("text", ""))
                elif p.get("type") == "image_url":
                    parts.append("[image]")
                elif p.get("type") == "image":
                    parts.append("[image]")
                elif "text" in p:
                    parts.append(str(p["text"]))
        return "\n".join(x for x in parts if x)
    if content is None:
        return ""
    return str(content)


def _tool_segments(tools) -> list[Segment]:
    """One segment per tool, not one blob.

    Tool schemas are re-sent verbatim on every turn and are often the largest
    single repeated cost in an agent run. Splitting them means the report can
    point at the one tool whose description is expensive, instead of hiding the
    whole set behind a single `tools` bar.
    """
    segs: list[Segment] = []
    for t in tools or []:
        body = t["function"] if isinstance(t, dict) and isinstance(t.get("function"), dict) else t
        segs.append(Segment("tools", json.dumps(body, sort_keys=True, ensure_ascii=False)))
    return segs


def segments_from_openai(messages: list[dict], tools: list[dict] | None) -> list[Segment]:
    segs: list[Segment] = _tool_segments(tools)
    for m in messages:
        role = m.get("role", "user")
        body = _text_of(m.get("content"))
        if role == "assistant" and m.get("tool_calls"):
            body += json.dumps(m["tool_calls"], sort_keys=True, ensure_ascii=False)
        if role == "tool":
            body = f"{m.get('tool_call_id', '')}\n{body}"
        kind = {"system": "system", "developer": "system", "tool": "tool"}.get(role, role)
        if kind not in ("system", "user", "assistant", "tool"):
            kind = "user"
        segs.append(Segment(kind, body))
    return segs


def segments_from_anthropic(
    messages: list[dict], system, tools: list[dict] | None
) -> list[Segment]:
    segs: list[Segment] = []
    if system:
        segs.append(Segment("system", _text_of(system)))
    segs.extend(_tool_segments(tools))
    for m in messages:
        role = m.get("role", "user")
        kind = "assistant" if role == "assistant" else "user"
        content = m.get("content")
        if isinstance(content, list):
            for p in content:
                if not isinstance(p, dict):
                    continue
                t = p.get("type")
                if t == "tool_result":
                    body = _text_of(p.get("content"))
                    segs.append(Segment("tool", f"{p.get('tool_use_id', '')}\n{body}"))
                elif t == "tool_use":
                    segs.append(
                        Segment("assistant", json.dumps(p, sort_keys=True, ensure_ascii=False))
                    )
                elif t == "image":
                    segs.append(Segment(kind, "[image]"))
                else:
                    segs.append(Segment(kind, _text_of(p.get("text", ""))))
        else:
            segs.append(Segment(kind, _text_of(content)))
    return segs


def segments_from_responses(input, instructions, tools) -> list[Segment]:
    """Responses API: flat `input` items, system prompt in `instructions`.

    Items are discriminated by `type`: messages carry a role, `function_call`
    items are the model's tool calls, `*_output` items are tool results, and
    everything else (reasoning, computer_call, mcp_call, ...) is passed through
    as an opaque assistant-side block. Unknown types are serialized whole, so a
    change in any field still changes the fingerprint.
    """
    segs: list[Segment] = []
    if instructions:
        segs.append(Segment("system", _text_of(instructions)))
    segs.extend(_tool_segments(tools))
    if isinstance(input, str):
        segs.append(Segment("user", input))
        return segs
    for item in input or []:
        if isinstance(item, str):
            segs.append(Segment("user", item))
            continue
        if not isinstance(item, dict):
            item = item.model_dump() if hasattr(item, "model_dump") else {"repr": str(item)}
        typ = item.get("type")
        if typ in (None, "message"):
            role = item.get("role", "user")
            kind = "system" if role in ("system", "developer") else role
            if kind not in ("system", "user", "assistant"):
                kind = "user"
            segs.append(Segment(kind, _text_of(item.get("content"))))
        elif typ == "function_call" or typ == "custom_tool_call":
            segs.append(Segment("assistant", json.dumps(item, sort_keys=True, ensure_ascii=False)))
        elif isinstance(typ, str) and typ.endswith("_output"):
            out = item.get("output", item.get("content", ""))
            text = (
                out
                if isinstance(out, str)
                else json.dumps(out, sort_keys=True, ensure_ascii=False)
            )
            segs.append(Segment("tool", f"{item.get('call_id', '')}\n{text}"))
        else:
            segs.append(Segment("assistant", json.dumps(item, sort_keys=True, ensure_ascii=False)))
    return segs


# ------------------------------------------------------------------- recorder


class _Recorder:
    def __init__(self, trace: Trace, path: str | None, keep_content: bool):
        self.trace = trace
        self.path = path
        self.keep_content = keep_content

    def write(self, call: Call) -> None:
        self.trace.add(call)
        if not self.path:
            return
        if not self.keep_content:
            for s in call.segments:
                s.text = s.text[:400]
        with open(self.path, "a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write(json.dumps({"name": self.trace.name, "meta": self.trace.meta}) + "\n")
            f.write(json.dumps(call.to_json(), ensure_ascii=False) + "\n")


def _usage_openai(resp) -> tuple[int, int, int]:
    u = getattr(resp, "usage", None)
    if u is None:
        return 0, 0, 0
    cached = 0
    details = getattr(u, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    return (
        getattr(u, "prompt_tokens", 0) or 0,
        getattr(u, "completion_tokens", 0) or 0,
        cached,
    )


def _usage_anthropic(resp) -> tuple[int, int, int]:
    u = getattr(resp, "usage", None)
    if u is None:
        return 0, 0, 0
    return (
        (getattr(u, "input_tokens", 0) or 0)
        + (getattr(u, "cache_read_input_tokens", 0) or 0)
        + (getattr(u, "cache_creation_input_tokens", 0) or 0),
        getattr(u, "output_tokens", 0) or 0,
        getattr(u, "cache_read_input_tokens", 0) or 0,
    )


def _usage_responses(resp) -> tuple[int, int, int]:
    u = getattr(resp, "usage", None)
    if u is None:
        return 0, 0, 0
    cached = 0
    details = getattr(u, "input_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    return (
        getattr(u, "input_tokens", 0) or 0,
        getattr(u, "output_tokens", 0) or 0,
        cached,
    )


def _patch_openai_responses(rec: _Recorder) -> None:
    try:
        from openai.resources.responses import Responses
    except Exception:
        return

    original = Responses.create

    def create(self, *args, **kwargs):
        resp = original(self, *args, **kwargs)
        if kwargs.get("stream"):
            return _tap_stream_responses(resp, kwargs, rec)
        inp, out, cached = _usage_responses(resp)
        rec.write(
            Call(
                model=kwargs.get("model") or getattr(resp, "model", "unknown"),
                segments=segments_from_responses(
                    kwargs.get("input"), kwargs.get("instructions"), kwargs.get("tools")
                ),
                input_tokens=inp,
                output_tokens=out,
                cached_tokens=cached,
                note=_server_side_note(kwargs),
            )
        )
        return resp

    _ORIGINALS.append((Responses, "create", original))
    Responses.create = create
    _ACTIVE.append(rec)


def _tap_stream_responses(stream, kwargs, rec: _Recorder):
    def gen():
        final = None
        for event in stream:
            if getattr(event, "type", None) in ("response.completed", "response.incomplete"):
                final = getattr(event, "response", None)
            yield event
        if final is not None:
            inp, out, cached = _usage_responses(final)
            rec.write(
                Call(
                    model=kwargs.get("model") or getattr(final, "model", "unknown"),
                    segments=segments_from_responses(
                        kwargs.get("input"), kwargs.get("instructions"), kwargs.get("tools")
                    ),
                    input_tokens=inp,
                    output_tokens=out,
                    cached_tokens=cached,
                    note=_server_side_note(kwargs),
                )
            )

    return gen()


def _server_side_note(kwargs) -> str | None:
    """previous_response_id / conversation moves context server-side.

    The usage still bills the whole history, but the request body only carries
    the new turn, so the per-segment split cannot be attributed. Say so instead
    of silently scaling a tiny context up to the full bill.
    """
    if kwargs.get("previous_response_id") or kwargs.get("conversation"):
        return "server-side context (previous_response_id/conversation): segment split unavailable"
    return None


def _patch_openai(rec: _Recorder) -> None:
    try:
        from openai.resources.chat.completions import Completions
    except Exception:
        return

    original = Completions.create

    def create(self, *args, **kwargs):
        stream = kwargs.get("stream")
        resp = original(self, *args, **kwargs)
        if stream:
            if not (kwargs.get("stream_options") or {}).get("include_usage"):
                _warn("streamed call skipped (set stream_options={'include_usage': True})")
                return resp
            return _tap_stream_openai(resp, kwargs, rec)
        inp, out, cached = _usage_openai(resp)
        segs = segments_from_openai(kwargs.get("messages") or [], kwargs.get("tools"))
        rec.write(
            Call(
                model=kwargs.get("model") or getattr(resp, "model", "unknown"),
                segments=segs,
                input_tokens=inp,
                output_tokens=out,
                cached_tokens=cached,
            )
        )
        return resp

    _ORIGINALS.append((Completions, "create", original))
    Completions.create = create
    _ACTIVE.append(rec)


def _tap_stream_openai(stream, kwargs, rec: _Recorder) -> None:
    def gen():
        usage = None
        for chunk in stream:
            u = getattr(chunk, "usage", None)
            if u is not None:
                usage = u
            yield chunk
        if usage is not None:
            cached = 0
            d = getattr(usage, "prompt_tokens_details", None)
            if d is not None:
                cached = getattr(d, "cached_tokens", 0) or 0
            rec.write(
                Call(
                    model=kwargs.get("model") or "unknown",
                    segments=segments_from_openai(
                        kwargs.get("messages") or [], kwargs.get("tools")
                    ),
                    input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    cached_tokens=cached,
                )
            )

    return gen()


def _patch_anthropic(rec: _Recorder) -> None:
    try:
        from anthropic.resources.messages import Messages
    except Exception:
        return

    original = Messages.create

    def create(self, *args, **kwargs):
        resp = original(self, *args, **kwargs)
        inp, out, cached = _usage_anthropic(resp)
        segs = segments_from_anthropic(
            kwargs.get("messages") or [], kwargs.get("system"), kwargs.get("tools")
        )
        rec.write(
            Call(
                model=kwargs.get("model") or getattr(resp, "model", "unknown"),
                segments=segs,
                input_tokens=inp,
                output_tokens=out,
                cached_tokens=cached,
            )
        )
        return resp

    _ORIGINALS.append((Messages, "create", original))
    Messages.create = create
    _ACTIVE.append(rec)


def watch(
    path: str | None = "ctxburn-trace.jsonl",
    name: str = "run",
    keep_content: bool = True,
) -> Trace:
    """Start recording every OpenAI / Anthropic call to `path` as JSONL."""
    trace = Trace(name=name, meta={"tool": "ctxburn.watch"})
    rec = _Recorder(trace, path, keep_content)
    _patch_openai(rec)
    _patch_openai_responses(rec)
    _patch_anthropic(rec)
    if not _ACTIVE:
        _warn("neither openai nor anthropic is importable; nothing to record")
    elif path:
        print(f"ctxburn: recording → {path}", file=sys.stderr)
    return trace


def unwatch() -> None:
    """Restore the patched SDK methods."""
    global _ACTIVE
    while _ORIGINALS:
        obj, attr, orig = _ORIGINALS.pop()
        with contextlib.suppress(Exception):
            setattr(obj, attr, orig)
    _ACTIVE = []
