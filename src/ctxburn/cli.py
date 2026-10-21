"""CLI: ctxburn report <trace.jsonl> -o report.html"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .analyze import analyze
from .model import Trace
from .render import CHARTS, render_html, summary_line


def _tok(v: int) -> str:
    return f"{v:,}"


def cmd_report(args: argparse.Namespace) -> int:
    trace = Trace.load(args.trace)
    report = analyze(trace)
    if not report.n_calls:
        print(f"no calls in {args.trace}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0

    html = render_html(report, lang=args.lang, chart=args.chart)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    print(summary_line(report, args.lang))
    for n in report.notes:
        print(f"  ! {n}")
    print()
    print(f"  input      {_tok(report.total_input_tokens)} tok")
    print(f"  fresh      {_tok(report.fresh_tokens)} tok")
    print(f"  replay     {_tok(report.replay_tokens)} tok")
    print(f"  cached     {_tok(report.total_cached_tokens)} tok")
    print(f"  output     {_tok(report.total_output_tokens)} tok")
    print()
    total = report.total_input_tokens or 1
    for kind, (tokens, replay, _) in sorted(report.by_kind.items(), key=lambda kv: -kv[1][0]):
        share = tokens / total
        rep = replay / tokens if tokens else 0
        print(f"  {kind:<10} {_tok(tokens):>8} tok  {share:>4.0%} of input  {rep:>4.0%} replay")
    if report.top_segments:
        print()
        print("  heaviest pieces")
        for s in report.top_segments[:5]:
            print(
                f"  {s['kind']:<10} {_tok(int(s['tokens'])):>8} tok  {s['sends']}x  "
                f"{s['preview'][:46]}"
            )
    print()
    print(f"→ {args.out}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .proxy import serve

    serve(
        port=args.port,
        out=args.out,
        openai_base=args.openai_base,
        anthropic_base=args.anthropic_base,
        default=args.default,
        name=args.name,
        quiet=args.quiet,
    )
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(args.root))
    from examples.mock_run import build  # type: ignore

    trace = build(lang=args.lang)
    trace.save(args.out)
    print(f"→ {args.out} ({len(trace.calls)} calls)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ctxburn", description=__doc__)
    p.add_argument("--version", action="version", version=f"ctxburn {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("report", help="render a trace to a single-file HTML report")
    rp.add_argument("trace", help="trace .jsonl produced by ctxburn.watch")
    rp.add_argument("-o", "--out", default="ctxburn-report.html")
    rp.add_argument("--json", action="store_true", help="print the report as JSON instead")
    rp.add_argument("--lang", choices=["en", "zh"], default="en", help="report language")
    rp.add_argument(
        "--chart", choices=list(CHARTS), default="flame", help="default chart to open on"
    )
    rp.set_defaults(func=cmd_report)

    sp = sub.add_parser(
        "serve", help="run a recording proxy so agents in any language can be profiled"
    )
    sp.add_argument("-p", "--port", type=int, default=8787)
    sp.add_argument("-o", "--out", default="ctxburn-trace.jsonl")
    sp.add_argument("--openai-base", default="https://api.openai.com", metavar="URL")
    sp.add_argument("--anthropic-base", default="https://api.anthropic.com", metavar="URL")
    sp.add_argument(
        "--default",
        choices=["anthropic", "openai"],
        default="anthropic",
        help="upstream for paths the router does not recognise",
    )
    sp.add_argument("--name", default="proxy", help="trace name")
    sp.add_argument("-q", "--quiet", action="store_true", help="no per-request logging")
    sp.set_defaults(func=cmd_serve)

    dp = sub.add_parser("demo", help="write the synthetic example trace")
    dp.add_argument("-o", "--out", default="ctxburn-demo.jsonl")
    dp.add_argument("--root", default=".", help=argparse.SUPPRESS)
    dp.add_argument("--lang", choices=["en", "zh"], default="en", help="language of the run")
    dp.set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
