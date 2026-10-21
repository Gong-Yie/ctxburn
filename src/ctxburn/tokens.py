"""Token estimation without a tokenizer dependency.

Exact token counts come from the provider's `usage` field -- that is what we
trust and what we calibrate against. This estimator only has one job: split a
call's *reported* input_tokens across the individual context segments in
rough proportion to their real size. Being off by a few percent on the split
is fine; being off on the total is not, and the total is always reported.
"""

from __future__ import annotations

# Wide CJK + kana + hangul blocks. These run roughly 1.6 chars per token,
# versus ~4 ASCII chars per token.
_CJK = (
    (0x1100, 0x11FF),  # hangul jamo
    (0x2E80, 0x2FDF),  # cjk radicals
    (0x3000, 0x30FF),  # cjk punctuation, hiragana, katakana
    (0x3400, 0x4DBF),  # cjk ext A
    (0x4E00, 0x9FFF),  # cjk unified
    (0xAC00, 0xD7AF),  # hangul syllables
    (0xF900, 0xFAFF),  # cjk compat
    (0xFF00, 0xFFEF),  # fullwidth forms
    (0x20000, 0x2FA1F),  # cjk ext B+
)

_CJK_CHARS_PER_TOKEN = 1.6
_ASCII_CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    """Rough token count. Monotonic in length, order-of-magnitude correct."""
    if not text:
        return 0
    cjk = 0
    asciiish = 0
    for ch in text:
        o = ord(ch)
        if o < 128:
            asciiish += 1
            continue
        for lo, hi in _CJK:
            if lo <= o <= hi:
                cjk += 1
                break
        else:
            asciiish += 0.6  # latin-1 accents, emoji (short multi-byte sequences)
    return max(1, round(cjk / _CJK_CHARS_PER_TOKEN + asciiish / _ASCII_CHARS_PER_TOKEN))


def estimate_messages(messages: list[dict]) -> int:
    """Estimate a raw chat-completions style message list."""
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate_tokens(str(part.get("text") or part.get("content") or ""))
        total += 4  # role + framing overhead per message
    return total
