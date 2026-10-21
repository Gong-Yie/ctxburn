"""Per-million-token USD prices. A snapshot, not a live feed.

Where these numbers come from: https://models.dev/api.json, vendor-provided
entries only (`openai`, `anthropic`, `google`, `deepseek`), read as $ per 1M
tokens with `cache_read` as the cached-input rate. Snapshot date is AS_OF.

Rates move. Anything not in this table falls back to FALLBACK and is flagged
`estimated` in the report -- fix it with --prices or --set-price rather than
trusting the fallback.

Note what the money actually affects: the replay *ratio* is price-independent,
because fresh and replayed tokens are billed at the same input rate. Only the
absolute dollar figures move when a price is wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

AS_OF = "2026-10-22"
SOURCE = "https://models.dev/api.json (vendor entries)"

# model -> (input $/Mtok, output $/Mtok, cached-input $/Mtok)
TABLE: dict[str, tuple[float, float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00, 1.25),
    "gpt-4o-mini": (0.15, 0.60, 0.075),
    "gpt-4.1": (2.00, 8.00, 0.50),
    "gpt-4.1-mini": (0.40, 1.60, 0.10),
    "gpt-4.1-nano": (0.10, 0.40, 0.025),
    "gpt-5.1": (1.25, 10.00, 0.125),
    "gpt-5.2": (1.75, 14.00, 0.175),
    "gpt-5.4": (2.50, 15.00, 0.25),
    "gpt-5.6": (4.00, 20.00, 0.40),
    "o3": (2.00, 8.00, 0.50),
    "o4-mini": (1.10, 4.40, 0.275),
    # Anthropic
    "claude-opus-4": (5.00, 25.00, 0.50),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-sonnet-4": (3.00, 15.00, 0.30),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-haiku-4": (1.00, 5.00, 0.10),
    # Google
    "gemini-2.5-pro": (1.25, 10.00, 0.125),
    "gemini-2.5-flash": (0.30, 2.50, 0.03),
    "gemini-3.6-flash": (0.75, 3.75, 0.075),
    # DeepSeek
    "deepseek-flash": (0.15, 0.60, 0.003),
    "deepseek-v4-pro": (0.66, 1.98, 0.022),
}

FALLBACK = "gpt-4o"


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cached: float
    estimated: bool = False
    matched: str = ""


def lookup(model: str, table: dict[str, tuple[float, float, float]] | None = None) -> Price:
    """Longest-prefix match so 'gpt-4o-2024-08-06' finds 'gpt-4o'."""
    tbl = table or TABLE
    m = (model or "").lower()
    best: str | None = None
    for name in tbl:
        if m == name or m.startswith(name):
            if best is None or len(name) > len(best):
                best = name
    if best:
        i, o, c = tbl[best]
        return Price(i, o, c, matched=best)
    i, o, c = tbl.get(FALLBACK) or TABLE[FALLBACK]
    return Price(i, o, c, estimated=True, matched=FALLBACK)


def parse_override(spec: str) -> tuple[str, Price]:
    """'gpt-4o=2.5,10,1.25' -> ('gpt-4o', Price(2.5, 10, 1.25))"""
    model, _, rest = spec.partition("=")
    parts = [p.strip() for p in rest.split(",")]
    if len(parts) != 3:
        raise ValueError(f"bad price override: {spec!r} (want model=in,out,cached)")
    return model.strip(), Price(*(float(p) for p in parts))


def load_prices(path: str) -> dict[str, tuple[float, float, float]]:
    """Load a price file, merged over the built-in table.

    Values are $ per 1M tokens. Either shape works::

        {"gpt-4o": [2.5, 10.0, 1.25]}
        {"my-model": {"input": 1, "output": 4, "cached": 0.1}}

    Keys starting with "_" are ignored, so a file can carry its own notes.
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out: dict[str, tuple[float, float, float]] = dict(TABLE)
    for model, val in raw.items():
        if model.startswith("_"):
            continue
        if isinstance(val, dict):
            out[model] = (
                float(val.get("input", 0.0)),
                float(val.get("output", 0.0)),
                float(val.get("cached", val.get("cache_read", 0.0))),
            )
        else:
            i, o, c = val
            out[model] = (float(i), float(o), float(c))
    return out
