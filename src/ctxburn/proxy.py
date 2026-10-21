"""A local recording proxy, so any language can be profiled.

    ctxburn serve -o run.jsonl
    ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
    OPENAI_BASE_URL=http://127.0.0.1:8787/v1 codex

The agent keeps talking to the same API it always did; only the hostname
changes. That is the whole point: it does not care whether the client is
Python, Node, Go or a shell script, and it does not depend on any tool's
private session format.

Scope: the caller's own machine, plain HTTP on loopback. Nothing is sent
anywhere except upstream, which is where it was already going.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq
from urllib.error import HTTPError, URLError

from .capture import (
    _Recorder,
    segments_from_anthropic,
    segments_from_openai,
    segments_from_responses,
)
from .model import Call, Trace

ANTHROPIC = "https://api.anthropic.com"
OPENAI = "https://api.openai.com"

_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "accept-encoding",  # ask for identity so usage stays parseable
}


def route(path: str, default: str = "anthropic") -> str:
    """Which upstream a path belongs to."""
    p = path.split("?", 1)[0].rstrip("/")
    if p.startswith("/v1/messages") or "/messages" in p:
        return "anthropic"
    if p.startswith("/v1/responses") or p.startswith("/responses"):
        return "openai"
    if "chat/completions" in p or "completions" in p:
        return "openai"
    if p.endswith("/models"):
        return "openai"
    return default


def usage_from_json(provider: str, data: dict) -> tuple[int, int, int]:
    """(input, output, cached) out of a provider response body."""
    u = data.get("usage") or {}
    if provider == "anthropic":
        cached = u.get("cache_read_input_tokens") or 0
        inp = (
            (u.get("input_tokens") or 0)
            + cached
            + (u.get("cache_creation_input_tokens") or 0)
        )
        return inp, u.get("output_tokens") or 0, cached
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
    if cached is None:
        cached = (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
    return (
        u.get("prompt_tokens") or u.get("input_tokens") or 0,
        u.get("completion_tokens") or u.get("output_tokens") or 0,
        cached or 0,
    )


class StreamTap:
    """Accumulates usage out of a server-sent-events stream."""

    def __init__(self, provider: str):
        self.provider = provider
        self.input = 0
        self.output = 0
        self.cached = 0
        self.seen = False
        self._buf = b""

    def feed(self, chunk: bytes) -> None:
        self._buf += chunk
        while b"\n" in self._buf:
            line, _, self._buf = self._buf.partition(b"\n")
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload in (b"", b"[DONE]"):
                continue
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            self._absorb(obj)

    def _absorb(self, obj: dict) -> None:
        kind = obj.get("type") or ""
        # Anthropic
        if kind == "message_start":
            msg = obj.get("message") or {}
            self._set(usage_from_json("anthropic", msg))
        elif kind == "message_delta" and obj.get("usage"):
            u = obj["usage"]
            self.output = u.get("output_tokens") or self.output
            self.seen = True
        # OpenAI responses
        elif kind in ("response.completed", "response.incomplete"):
            resp = obj.get("response") or {}
            self._set(usage_from_json("openai", resp))
        # OpenAI chat completions (last chunk when include_usage is set)
        elif obj.get("usage"):
            self._set(usage_from_json("openai", obj))

    def _set(self, triple: tuple[int, int, int]) -> None:
        inp, out, cached = triple
        if inp:
            self.input = inp
        if out:
            self.output = out
        self.cached = cached or self.cached
        self.seen = True

    def result(self) -> tuple[int, int, int] | None:
        return (self.input, self.output, self.cached) if self.seen else None


def segments_for(provider: str, body: dict):
    if provider == "anthropic":
        return segments_from_anthropic(
            body.get("messages") or [], body.get("system"), body.get("tools")
        )
    if "input" in body or "instructions" in body:
        return segments_from_responses(
            body.get("input"), body.get("instructions"), body.get("tools")
        )
    return segments_from_openai(body.get("messages") or [], body.get("tools"))


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ctxburn"

    # injected by serve()
    recorder: _Recorder
    upstreams: dict[str, str]
    default: str
    timeout: float
    quiet: bool = False

    def log_message(self, fmt, *args):  # noqa: A003 - silence default access log
        if not self.quiet:
            sys.stderr.write("ctxburn proxy: %s\n" % (fmt % args))

    # ---------------------------------------------------------------- plumbing
    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _forward_headers(self) -> dict:
        out = {}
        for k, v in self.headers.items():
            if k.lower() not in _HOP:
                out[k] = v
        out["Accept-Encoding"] = "identity"
        return out

    def _upstream_url(self, provider: str) -> str:
        base = self.upstreams[provider].rstrip("/")
        return base + self.path

    def _record(self, provider: str, body: dict, usage: tuple[int, int, int], note=None):
        inp, out, cached = usage
        call = Call(
            model=body.get("model") or "unknown",
            segments=segments_for(provider, body),
            input_tokens=inp,
            output_tokens=out,
            cached_tokens=cached,
            note=note,
        )
        self.recorder.write(call)
        if not self.quiet:
            sys.stderr.write(
                f"ctxburn: {call.model}  in={inp:,} out={out:,} "
                f"({len(call.segments)} segments)\n"
            )

    # ------------------------------------------------------------------ verbs
    def do_POST(self):  # noqa: N802
        provider = route(self.path, self.default)
        raw = self._read_body()
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}

        # make streamed chat completions report usage without touching the client
        if provider == "openai" and body.get("stream") and "messages" in body:
            opts = body.get("stream_options") or {}
            if not opts.get("include_usage"):
                opts["include_usage"] = True
                body["stream_options"] = opts
                raw = json.dumps(body).encode()

        req = urlreq.Request(
            self._upstream_url(provider),
            data=raw,
            headers=self._forward_headers(),
            method="POST",
        )
        try:
            upstream = urlreq.urlopen(req, timeout=self.timeout)
        except HTTPError as e:
            self._relay_error(e.code, e.read())
            return
        except URLError as e:
            self._relay_error(502, json.dumps({"error": str(e.reason)}).encode())
            return

        ctype = upstream.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            self._stream(upstream, provider, body)
        else:
            payload = upstream.read()
            # record before replying: a client that sees the response must not
            # outrun the trace, or a fast caller reads a file that is not there yet
            try:
                data = json.loads(payload)
            except Exception:
                data = {}
            usage = usage_from_json(provider, data)
            if usage[0] or usage[1]:
                self._record(provider, body, usage)
            self.send_response(upstream.status)
            self.send_header("Content-Type", ctype or "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        provider = route(self.path, self.default)
        req = urlreq.Request(
            self._upstream_url(provider), headers=self._forward_headers(), method="GET"
        )
        try:
            upstream = urlreq.urlopen(req, timeout=self.timeout)
        except HTTPError as e:
            self._relay_error(e.code, e.read())
            return
        except URLError as e:
            self._relay_error(502, json.dumps({"error": str(e.reason)}).encode())
            return
        payload = upstream.read()
        self.send_response(upstream.status)
        self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ----------------------------------------------------------------- output
    def _relay_error(self, code: int, payload: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _stream(self, upstream, provider: str, body: dict):
        self.send_response(upstream.status)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        tap = StreamTap(provider)
        try:
            while True:
                chunk = upstream.read1(8192) if hasattr(upstream, "read1") else upstream.read(8192)
                if not chunk:
                    break
                tap.feed(chunk)
                self.wfile.write(b"%X\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            usage = tap.result()
            if usage and (usage[0] or usage[1]):
                self._record(provider, body, usage)
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass


def serve(
    port: int = 8787,
    out: str = "ctxburn-trace.jsonl",
    openai_base: str = OPENAI,
    anthropic_base: str = ANTHROPIC,
    default: str = "anthropic",
    name: str = "proxy",
    timeout: float = 600.0,
    quiet: bool = False,
) -> None:
    trace = Trace(name=name, meta={"tool": "ctxburn.serve"})
    rec = _Recorder(trace, out, keep_content=True)
    handler = type(
        "Handler",
        (_Handler,),
        {
            "recorder": rec,
            "upstreams": {"openai": openai_base, "anthropic": anthropic_base},
            "default": default,
            "timeout": timeout,
            "quiet": quiet,
        },
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    host = f"http://127.0.0.1:{port}"
    print(f"ctxburn: recording proxy on {host} → {out}", file=sys.stderr)
    print("", file=sys.stderr)
    print(f"  claude code   ANTHROPIC_BASE_URL={host}", file=sys.stderr)
    print(f"  openai-style  OPENAI_BASE_URL={host}/v1", file=sys.stderr)
    print("                (any SDK, any language that honours a base url)", file=sys.stderr)
    print(f"  upstream      anthropic → {anthropic_base}", file=sys.stderr)
    print(f"                openai    → {openai_base}", file=sys.stderr)
    print("", file=sys.stderr)
    print("ctrl-c to stop; the trace is written as calls complete.", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nctxburn: stopped", file=sys.stderr)
    finally:
        httpd.server_close()


def run_in_background(**kwargs) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Used by tests: start the proxy on a free port and hand back the server."""
    trace = Trace(name=kwargs.get("name", "proxy"))
    rec = _Recorder(trace, kwargs.get("out"), keep_content=True)
    handler = type(
        "Handler",
        (_Handler,),
        {
            "recorder": rec,
            "upstreams": {
                "openai": kwargs.get("openai_base", OPENAI),
                "anthropic": kwargs.get("anthropic_base", ANTHROPIC),
            },
            "default": kwargs.get("default", "anthropic"),
            "timeout": 30.0,
            "quiet": True,
        },
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t
