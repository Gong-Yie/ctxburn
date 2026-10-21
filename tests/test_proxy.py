import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ctxburn.model import Trace
from ctxburn.proxy import StreamTap, route, run_in_background, usage_from_json

SSE_ANTHROPIC = [
    {
        "type": "message_start",
        "message": {"usage": {"input_tokens": 1000, "cache_read_input_tokens": 0}},
    },
    {"type": "content_block_delta", "delta": {"text": "hel"}},
    {"type": "content_block_delta", "delta": {"text": "lo"}},
    {"type": "message_delta", "usage": {"output_tokens": 42}},
]

JSON_ANTHROPIC = {
    "id": "msg_1",
    "model": "claude-sonnet-4",
    "usage": {"input_tokens": 1234, "cache_read_input_tokens": 200, "output_tokens": 56},
}

JSON_OPENAI = {
    "id": "cc_1",
    "model": "gpt-4o",
    "usage": {"prompt_tokens": 500, "completion_tokens": 30,
              "prompt_tokens_details": {"cached_tokens": 100}},
}


class _Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    last_body: dict = {}

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).last_body = body
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            events = SSE_ANTHROPIC if "messages" in self.path and "chat" not in self.path else [
                {"choices": [{"delta": {"content": "hi"}}]},
                {"choices": [], "usage": {"prompt_tokens": 700, "completion_tokens": 20}},
            ]
            for ev in events:
                data = f"data: {json.dumps(ev)}\n\n".encode()
                self.wfile.write(b"%X\r\n" % len(data) + data + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        payload = json.dumps(
            JSON_OPENAI if "chat" in self.path else JSON_ANTHROPIC
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def upstream():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def proxy(upstream, tmp_path):
    out = tmp_path / "trace.jsonl"
    httpd, _ = run_in_background(
        out=str(out), openai_base=upstream, anthropic_base=upstream
    )
    yield f"http://127.0.0.1:{httpd.server_address[1]}", out
    httpd.shutdown()
    httpd.server_close()


def _post(url, body, headers=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


# ------------------------------------------------------------------ units


def test_route_picks_the_right_upstream():
    assert route("/v1/messages") == "anthropic"
    assert route("/v1/messages?beta=true") == "anthropic"
    assert route("/v1/chat/completions") == "openai"
    assert route("/v1/responses") == "openai"
    assert route("/v1/models") == "openai"
    assert route("/some/relay/path") == "anthropic"
    assert route("/some/relay/path", default="openai") == "openai"


def test_usage_anthropic_counts_cache_reads_as_input():
    inp, out, cached = usage_from_json("anthropic", JSON_ANTHROPIC)
    assert (inp, out, cached) == (1434, 56, 200)


def test_usage_openai_details_shape():
    inp, out, cached = usage_from_json("openai", JSON_OPENAI)
    assert (inp, out, cached) == (500, 30, 100)


def test_stream_tap_reads_split_chunks():
    """Events arrive in arbitrary byte boundaries; the tap must not care."""
    blob = b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in SSE_ANTHROPIC)
    tap = StreamTap("anthropic")
    for i in range(0, len(blob), 7):
        tap.feed(blob[i : i + 7])
    assert tap.result() == (1000, 42, 0)


def test_stream_tap_openai_chat_usage():
    tap = StreamTap("openai")
    tap.feed(b'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":3}}\n\n')
    assert tap.result() == (9, 3, 0)


def test_stream_tap_ignores_junk():
    tap = StreamTap("anthropic")
    tap.feed(b": keepalive\n\n")
    tap.feed(b"data: not-json\n\n")
    assert tap.result() is None


# -------------------------------------------------------------- end to end


def test_non_streaming_anthropic_call_is_recorded(proxy):
    base, out = proxy
    body = {
        "model": "claude-sonnet-4",
        "system": "you are a bot",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "read", "input_schema": {"type": "object"}}],
    }
    resp = _post(f"{base}/v1/messages", body)
    assert json.loads(resp)["id"] == "msg_1"

    trace = Trace.load(str(out))
    assert len(trace.calls) == 1
    call = trace.calls[0]
    assert call.input_tokens == 1434
    assert call.output_tokens == 56
    assert call.cached_tokens == 200
    kinds = [s.kind for s in call.segments]
    assert kinds == ["system", "tools", "user"]


def test_streaming_anthropic_call_is_recorded(proxy):
    base, out = proxy
    body = {
        "model": "claude-sonnet-4",
        "system": "sys",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    _post(f"{base}/v1/messages", body)

    trace = Trace.load(str(out))
    assert len(trace.calls) == 1
    assert trace.calls[0].input_tokens == 1000
    assert trace.calls[0].output_tokens == 42


def test_chat_completion_gets_include_usage_injected(proxy):
    base, out = proxy
    _post(
        f"{base}/v1/chat/completions",
        {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    )
    assert _Upstream.last_body["stream_options"]["include_usage"] is True
    trace = Trace.load(str(out))
    assert trace.calls[0].input_tokens == 700
    assert trace.calls[0].output_tokens == 20


def test_responses_body_is_parsed_as_flat_items(proxy):
    base, out = proxy
    _post(
        f"{base}/v1/responses",
        {
            "model": "gpt-4o",
            "instructions": "be brief",
            "input": [
                {"role": "user", "content": "hello"},
                {"type": "function_call", "call_id": "c1", "name": "grep", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c1", "output": "found"},
            ],
        },
    )
    call = Trace.load(str(out)).calls[0]
    assert [s.kind for s in call.segments] == ["system", "user", "assistant", "tool"]


def test_two_calls_share_a_trace_and_replay_is_detected(proxy):
    base, out = proxy
    body = {
        "model": "claude-sonnet-4",
        "system": "you are a bot",
        "messages": [{"role": "user", "content": "hi"}],
    }
    _post(f"{base}/v1/messages", body)
    body["messages"].append({"role": "assistant", "content": "hello"})
    _post(f"{base}/v1/messages", body)

    from ctxburn.analyze import analyze

    r = analyze(Trace.load(str(out)))
    assert r.n_calls == 2
    assert r.replay_tokens > 0
    sys_first = r.calls[1].segments[0]
    assert sys_first.replay and sys_first.first_seen_at == 0
