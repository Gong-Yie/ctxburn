"""A synthetic but realistic 8-turn coding agent run, for tests and demos.

No network, no API key. It reproduces the shape that makes agents expensive:
one big system prompt plus tool schemas, re-sent verbatim on every single turn,
while the message history grows.

    PYTHONPATH=src python examples/mock_run.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ctxburn.analyze import analyze  # noqa: E402
from ctxburn.model import Call, Segment, Trace  # noqa: E402
from ctxburn.tokens import estimate_tokens  # noqa: E402

SYSTEM = """You are a senior software engineer operating inside a repository.

Rules of engagement:
- Read before you write. Never edit a file you have not opened in this session.
- Prefer the smallest change that makes the failing test pass.
- Do not refactor unrelated code. Do not reformat. Do not add comments that restate the code.
- Run the test suite after every edit. If it fails, read the failure and fix the cause, not the assertion.
- Keep your final message to what changed and why. No preamble.

Tool discipline:
- Use grep_search to locate symbols instead of guessing paths.
- Use read_file with explicit offsets for files over 400 lines.
- Never dump a whole file into context unless you intend to rewrite it.
- If a command produces more than 200 lines, pipe it through a filter.

Safety:
- Never force push. Never touch secrets. Never delete files outside the workspace.
- If a task requires network access, ask first.

Working notes:
- The repository uses pytest with a shared session fixture in conftest.py.
- Tests marked `slow` are excluded from the default run.
- CI runs on three Python versions; anything version-specific must be guarded.

Definition of done:
1. The named test passes in isolation and in the full suite.
2. No new warnings.
3. The change is described in one sentence.

Failure handling:
- If three attempts at the same error fail, stop and report what you learned.
- If a tool returns an error, retry once with corrected arguments, then escalate.

Context discipline:
- Summarize large tool outputs before reasoning over them.
- Drop information that is no longer relevant; the window is finite.
- When in doubt about intent, ask a short question instead of assuming.

Style:
- Be terse. Engineers reading this are paying by the second.
- No emoji. No exclamation marks. No "Great question".
- Quote file paths in backticks.
"""

TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "description": "Read a file from the repository. Supports offset and limit for large files. Returns line-numbered content.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the repository root."},
                "offset": {"type": "integer", "description": "First line to return, 1-indexed."},
                "limit": {"type": "integer", "description": "Maximum number of lines to return, default 2000."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "grep_search",
        "description": "Search the repository for a regular expression. Returns file paths, line numbers, and matching lines.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression to search for."},
                "path": {"type": "string", "description": "Directory to search, defaults to the repository root."},
                "file_glob": {"type": "string", "description": "Optional glob filter such as '*.py'."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace an exact string in a file with a new string. The old string must occur exactly once.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the repository root."},
                "old_string": {"type": "string", "description": "Text to replace. Must be unique in the file."},
                "new_string": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run pytest with the given arguments. Returns stdout, stderr, and the exit code.",
        "parameters": {
            "type": "object",
            "properties": {
                "args": {"type": "string", "description": "Arguments passed to pytest, e.g. 'tests/test_auth.py -x'."},
                "timeout": {"type": "integer", "description": "Seconds before the command is killed, default 120."},
            },
            "required": ["args"],
        },
    },
    {
        "name": "list_dir",
        "description": "List the entries of a directory with their types and sizes.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path relative to the repository root."},
            },
            "required": ["path"],
        },
    },
]

STEPS: list[tuple[str, str, str]] = [
    # (assistant text / tool call, tool name, tool result)
    (
        "I'll start by locating the flaky test and its fixture.",
        "grep_search",
        "tests/test_auth.py:42: def test_refresh_token_expiry(test_client, frozen_clock):\n"
        "tests/test_auth.py:43:     token = issue_token(ttl=timedelta(seconds=1))\n"
        "tests/test_auth.py:44:     frozen_clock.advance(timedelta(seconds=2))\n"
        "tests/test_auth.py:45:     assert is_expired(token)\n"
        "tests/conftest.py:12: @pytest.fixture(scope='session')\n"
        "tests/conftest.py:13: def frozen_clock() -> FrozenClock:\n"
        "tests/conftest.py:14:     return FrozenClock(datetime(2024, 1, 1, tzinfo=UTC))",
    ),
    (
        "The fixture is session-scoped. Advancing a shared clock explains the flakiness under -p xdist.",
        "read_file",
        " 1 | import pytest\n 2 | from datetime import datetime, timedelta\n 3 | from auth.tokens import issue_token, is_expired\n"
        " 4 |\n 5 | def test_refresh_token_roundtrip(test_client):\n 6 |     token = issue_token(ttl=timedelta(minutes=5))\n"
        " 7 |     assert not is_expired(token)\n 8 |\n 9 | def test_refresh_token_expiry(test_client, frozen_clock):\n"
        "10 |     token = issue_token(ttl=timedelta(seconds=1))\n11 |     frozen_clock.advance(timedelta(seconds=2))\n"
        "12 |     assert is_expired(token)\n13 |\n14 | def test_revoked_token_rejected(test_client, frozen_clock):\n"
        "15 |     token = issue_token(ttl=timedelta(minutes=5))\n16 |     revoke(token)\n17 |     assert is_expired(token)",
    ),
    (
        "Confirmed. I'll make the clock function-scoped so each test gets a fresh one.",
        "edit_file",
        "ok: replaced 3 lines in tests/conftest.py",
    ),
    (
        "Running the failing test in isolation first.",
        "run_tests",
        "collected 1 item\n\ntests/test_auth.py .                                                        [100%]\n\n"
        "1 passed in 0.31s",
    ),
    (
        "Passes alone. Now the full suite, where the ordering matters.",
        "run_tests",
        "collected 418 items\n\ntests/test_auth.py .......                                                 [  1%]\n"
        "tests/test_tokens.py ....................                                  [  6%]\n"
        "...\n418 passed, 12 skipped, 3 deselected in 41.7s",
    ),
]


def build(name: str = "fix-flaky-test", model: str = "claude-sonnet-4") -> Trace:
    trace = Trace(name=name, meta={"source": "synthetic", "task": "fix flaky auth test"})
    system = Segment("system", SYSTEM)
    tools = [Segment("tools", __import__("json").dumps(t, indent=2)) for t in TOOL_SCHEMAS]

    convo: list[Segment] = [
        Segment(
            "user",
            "tests/test_auth.py::test_refresh_token_expiry fails about one run in five on CI. "
            "Find out why and fix it. Do not change the assertion.",
        )
    ]

    for step in range(len(STEPS) + 1):
        segs = [system, *tools, *convo]
        input_tokens = sum(estimate_tokens(s.text) for s in segs)
        last = step == len(STEPS)
        call = Call(
            model=model,
            segments=segs,
            input_tokens=input_tokens,
            output_tokens=estimate_tokens("x" * (1400 if last else 220)),
            latency_ms=2400 + step * 310,
        )
        trace.add(call)
        if last:
            break
        thought, tool_name, result = STEPS[step]
        convo.append(Segment("assistant", f"{thought}\n<tool_call name=\"{tool_name}\">"))
        convo.append(Segment("tool", result))

    return trace


if __name__ == "__main__":
    t = build()
    r = analyze(t)
    print(f"calls           {r.n_calls}")
    print(f"input tokens    {r.total_input_tokens:,}")
    print(f"total cost      ${r.total_cost:.4f}")
    print(f"  fresh         ${r.cost_fresh:.4f}")
    print(f"  replay        ${r.cost_replay:.4f}")
    print(f"  cached        ${r.cost_cached:.4f}")
    print(f"  output        ${r.cost_output:.4f}")
    print(f"replay ratio    {r.replay_ratio:.1%}")
    print(f"growth          {r.context_growth:.2f}x")
    for k, v in sorted(r.by_kind.items(), key=lambda kv: -kv[1][1]):
        print(f"  {k:10s} {int(v[0]):>8,} tok  ${v[1]:.4f}")
