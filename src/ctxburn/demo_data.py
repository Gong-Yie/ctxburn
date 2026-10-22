"""A synthetic but realistic 8-turn coding agent run, for demos.

Shipped inside the package so `ctxburn demo` works from a wheel install.

No network, no API key. It reproduces the shape that makes agents expensive:
one big system prompt plus tool schemas, re-sent verbatim on every single turn,
while the message history grows.
"""

from __future__ import annotations

import json

from .analyze import analyze
from .model import Call, Segment, Trace
from .tokens import estimate_tokens

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


SYSTEM_ZH = """你是一名在代码仓库里工作的资深软件工程师。

行动准则：
- 先读再写。没打开过的文件不许改。
- 用最小的改动让失败的测试通过。
- 不重构无关代码，不重排版，不写复述代码的注释。
- 每次修改后跑测试。失败了就读报错、修原因，不要去修断言。
- 最终消息只说改了什么、为什么。不要开场白。

工具使用规范：
- 用 grep_search 定位符号，不要猜路径。
- 超过 400 行的文件，用 read_file 带 offset 读。
- 除非准备整个重写，不要把整个文件塞进上下文。
- 命令输出超过 200 行就先过滤再说。

安全要求：
- 不许强推。不许碰密钥。不许删工作区以外的文件。
- 任务需要联网时，先问。

工作备忘：
- 仓库用 pytest，共享 fixture 在 conftest.py。
- 标记 slow 的测试默认不跑。
- CI 跑三个 Python 版本，版本相关的写法必须加判断。

完成标准：
1. 指定的测试单跑和全量跑都过。
2. 没有新增警告。
3. 改动能用一句话说清。

失败处理：
- 同一个错误试了三次还搞不定，停下来，说清楚你摸到了什么。
- 工具报错先改参数重试一次，再往上报。

上下文管理：
- 大的工具输出先总结再推理。
- 不再相关的信息要丢掉，窗口是有限的。
- 拿不准意图时，问一句短的，别自行假设。

风格：
- 简短。读你消息的人是按秒付费的。
- 不用 emoji，不用感叹号，不说"好问题"。
- 文件路径用反引号括起来。
"""

TASK_ZH = (
    "tests/test_auth.py::test_refresh_token_expiry 在 CI 上大约每五次挂一次。"
    "找出原因并修掉，不要改断言。"
)

_ZH_THOUGHTS = [
    "先定位这个不稳定的测试和它用的 fixture。",
    "fixture 是 session 级的。多个测试共享同一个时钟，这解释了 -p xdist 下的偶发失败。",
    "确认了。把时钟改成 function 级，每个测试拿一个新的。",
    "先单独跑这个失败的测试。",
    "单跑过了。现在跑全量，顺序敏感的问题应该在这里出来。",
]

STEPS_ZH = [(zh, name, result) for zh, (_, name, result) in zip(_ZH_THOUGHTS, STEPS, strict=True)]

TASK = (
    "tests/test_auth.py::test_refresh_token_expiry fails about one run in five on CI. "
    "Find out why and fix it. Do not change the assertion."
)


def build(name: str = "fix-flaky-test", model: str = "claude-sonnet-4", lang: str = "en") -> Trace:
    zh = lang == "zh"
    system_text = SYSTEM_ZH if zh else SYSTEM
    steps = STEPS_ZH if zh else STEPS
    task = TASK_ZH if zh else TASK

    trace = Trace(name=name, meta={"source": "synthetic", "task": "fix flaky auth test", "lang": lang})
    system = Segment("system", system_text)
    tools = [Segment("tools", json.dumps(t, indent=2)) for t in TOOL_SCHEMAS]

    convo: list[Segment] = [Segment("user", task)]

    for step in range(len(steps) + 1):
        segs = [system, *tools, *convo]
        input_tokens = sum(estimate_tokens(s.text) for s in segs)
        last = step == len(steps)
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
        thought, tool_name, result = steps[step]
        convo.append(Segment("assistant", f"{thought}\n<tool_call name=\"{tool_name}\">"))
        convo.append(Segment("tool", result))

    return trace


if __name__ == "__main__":  # pragma: no cover
    t = build()
    r = analyze(t)
    print(f"calls           {r.n_calls}")
    print(f"input tokens    {r.total_input_tokens:,}")
    print(f"replay ratio    {r.replay_ratio:.1%}")
    print(f"growth          {r.context_growth:.2f}x")
    for k, v in sorted(r.by_kind.items(), key=lambda kv: -kv[1][0]):
        print(f"  {k:10s} {int(v[0]):>8,} tok")
