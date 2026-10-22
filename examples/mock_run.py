"""Run the synthetic 8-turn demo trace from the source tree.

The data itself lives in the package (`ctxburn.demo_data`) so that
`ctxburn demo` works after a plain `pip install`. This script only exists
for people who cloned the repo and want to poke at it:

    PYTHONPATH=src python examples/mock_run.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from ctxburn.analyze import analyze  # noqa: E402
from ctxburn.demo_data import build  # noqa: E402


def main() -> None:
    trace = build()
    report = analyze(trace)
    print(f"calls           {report.n_calls}")
    print(f"input tokens    {report.total_input_tokens:,}")
    print(f"replay ratio    {report.replay_ratio:.1%}")
    print(f"growth          {report.context_growth:.2f}x")
    for kind, (tokens, replay, _) in sorted(report.by_kind.items(), key=lambda kv: -kv[1][0]):
        print(f"  {kind:10s} {int(tokens):>8,} tok  {replay:>4.0%} replay")


if __name__ == "__main__":
    main()
