import json
import re

from ctxburn.analyze import analyze
from ctxburn.model import Call, Segment, Trace
from ctxburn.render import CHARTS, render_html, summary_line


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
        for chart in CHARTS:
            html = render_html(_report(), lang=lang, chart=chart)
            assert set(re.findall(r"@@[A-Z_]+@@", html)) == set(), (lang, chart)


def test_zh_report_is_actually_chinese():
    html = render_html(_report(), lang="zh")
    assert 'lang="zh"' in html
    assert "重发占比" in html
    assert "谁在付账" in html
    assert "输入 token" in html


def test_en_report_stays_english():
    html = render_html(_report(), lang="en")
    assert 'lang="en"' in html
    assert "replay share" in html
    assert "who owns the bill" in html


def test_unknown_lang_falls_back_to_english():
    assert 'lang="en"' in render_html(_report(), lang="klingon")


def test_summary_line_localized():
    r = _report()
    assert "次调用" in summary_line(r, "zh")
    assert "calls" in summary_line(r, "en")


def test_kind_names_stay_english_in_zh():
    html = render_html(_report(), lang="zh")
    assert ">system<" in html
    assert ">tools<" in html


def test_no_dollar_signs_anywhere():
    for lang in ("en", "zh"):
        html = render_html(_report(), lang=lang)
        assert "$" not in html.split("<script>")[0]


def test_all_four_charts_are_reachable():
    html = render_html(_report())
    buttons = re.findall(r'data-chart="(\w+)"', html)
    assert set(buttons) == set(CHARTS)
    assert len(buttons) == 4


def test_chart_arg_selects_default_view():
    for c in CHARTS:
        html = render_html(_report(), chart=c)
        assert f'"chart":"{c}"' in html
        assert f'class="tab on" data-chart="{c}"' in html


def test_bad_chart_falls_back_to_flame():
    assert '"chart":"flame"' in render_html(_report(), chart="scatter")


def test_data_and_canvas_present():
    html = render_html(_report())
    assert 'id="chart"' in html
    assert "const D = {" in html
    payload = json.loads(re.search(r"const D = (\{.*?\});\nconst UI", html, re.S).group(1))
    assert payload["n_calls"] == 4
    assert payload["fresh_tokens"] + payload["replay_tokens"] == payload["total_input_tokens"]
