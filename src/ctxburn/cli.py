"""CLI: ctxburn report <trace.jsonl> -o report.html"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .analyze import analyze
from .model import Trace
from .pricing import parse_override
from .render import render_html, summary_line


def _money(v: float) -> str:
    if v >= 1:
        return f"${v:,.2f}"
    if v >= 0.01:
        return f"${v:.3f}"
    return f"${v:.4f}"


def cmd_report(args: argparse.Namespace) -> int:
    trace = Trace.load(args.trace)
    overrides = dict(parse_override(s) for s in args.set_price)
    report = analyze(trace, overrides)
    if not report.n_calls:
        print(f"no calls in {args.trace}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0

    html = render_html(report, lang=args.lang)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    print(summary_line(report, args.lang))
    for n in report.notes:
        print(f"  ! {n}")
    print()
    print(f"  total      {_money(report.total_cost)}")
    print(f"  fresh      {_money(report.cost_fresh)}")
    print(f"  replay     {_money(report.cost_replay)}")
    print(f"  cached     {_money(report.cost_cached)}")
    print(f"  output     {_money(report.cost_output)}")
    print()
    for kind, (tokens, cost, rep) in sorted(report.by_kind.items(), key=lambda kv: -kv[1][1]):
        share = rep / tokens if tokens else 0
        print(f"  {kind:<10} {int(tokens):>8,} tok  {_money(cost):>8}  {share:>4.0%} replay")
    if report.top_segments:
        print()
        print("  heaviest pieces")
        for s in report.top_segments[:5]:
            print(
                f"  {s['kind']:<10} {int(s['tokens']):>8,} tok  {s['sends']}x  "
                f"{_money(s['cost']):>8}  {s['preview'][:46]}"
            )
    print()
    print(f"→ {args.out}")
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
    rp.add_argument("trace", help="trace .jsonl produced by ctxburn run")
    rp.add_argument("-o", "--out", default="ctxburn-report.html")
    rp.add_argument("--set-price", action="append", default=[], metavar="MODEL=IN,OUT,CACHED")
    rp.add_argument("--json", action="store_true", help="print the report as JSON instead")
    rp.add_argument("--lang", choices=["en", "zh"], default="en", help="report language")
    rp.set_defaults(func=cmd_report)

    dp = sub.add_parser("demo", help="write the synthetic example trace")
    dp.add_argument("-o", "--out", default="ctxburn-demo.jsonl")
    dp.add_argument("--root", default=".", help=argparse.SUPPRESS)
    dp.add_argument(
        "--lang", choices=["en", "zh"], default="en", help="language of the synthetic run"
    )
    dp.set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
