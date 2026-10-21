import re

from ctxburn.analyze import analyze
from ctxburn.model import Call, Segment, Trace
from ctxburn.render import render_html, summary_line


def _report(n=4):
    t = Trace(name="r")
    convo = [Segment("user", "修一下这个失败的测试")]
    for i in range(n):
        segs = [Segment("system", "你是工程师。" * 80), Segment("tools", '{"name":"read"}'), *convo]
        t.add(Call(model="gpt-4o", segments=segs, input_tokens=900 + i * 120, output_tokens=60))
        convo.append(Segment("assistant", f"第 {i} 步"))
    return analyze(t)


def test_no_placeholder_survives_either_language():
    for lang in ("en", "zh"):
        html = render_html(_report(), lang=lang)
        leftovers = set(re.findall(r"@@[A-Z_]+@@", html))
        assert leftovers == set(), (lang, leftovers)


def test_zh_report_is_actually_chinese():
    html = render_html(_report(), lang="zh")
    assert 'lang="zh"' in html
    assert "重发占比" in html
    assert "谁在付账" in html
    assert "总花费" in html


def test_en_report_stays_english():
    html = render_html(_report(), lang="en")
    assert 'lang="en"' in html
    assert "replay share" in html
    assert "who owns the bill" in html


def test_unknown_lang_falls_back_to_english():
    html = render_html(_report(), lang="klingon")
    assert 'lang="en"' in html


def test_summary_line_localized():
    r = _report()
    assert "%" in summary_line(r, "zh")
    assert "次调用" in summary_line(r, "zh")
    assert "calls" in summary_line(r, "en")


def test_kind_names_stay_english_in_zh():
    """They are identifiers from the trace file, not prose."""
    html = render_html(_report(), lang="zh")
    assert ">system<" in html
    assert ">tools<" in html


def test_canvas_and_data_present():
    html = render_html(_report(), lang="zh")
    assert 'id="flame"' in html
    assert "const D = {" in html
    assert '"ui"' in html
