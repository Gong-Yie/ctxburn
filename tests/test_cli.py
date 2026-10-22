"""End-to-end CLI checks: the commands a wheel user actually types."""

from ctxburn.cli import main


def test_demo_then_report(tmp_path):
    trace = tmp_path / "demo.jsonl"
    out = tmp_path / "report.html"

    assert main(["demo", "-o", str(trace)]) == 0
    assert trace.exists() and trace.stat().st_size > 0

    assert main(["report", str(trace), "-o", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "<html" in html
    assert "@@" not in html


def test_demo_json_report(capsys, tmp_path):
    trace = tmp_path / "demo.jsonl"
    assert main(["demo", "-o", str(trace)]) == 0
    assert main(["report", str(trace), "--json"]) == 0
    printed = capsys.readouterr().out
    assert '"n_calls"' in printed


def test_report_missing_file_returns_nonzero():
    assert main(["report", "does-not-exist.jsonl", "-o", "nope.html"]) != 0
