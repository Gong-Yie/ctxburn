import json

from ctxburn.pricing import AS_OF, TABLE, load_prices, lookup, parse_override


def test_every_builtin_price_is_plausible():
    """A $/Mtok typo (10x, or a per-1K number) is the easy mistake; catch it."""
    for model, (inp, out, cached) in TABLE.items():
        assert 0.01 <= inp <= 200, model
        assert 0.01 <= out <= 600, model
        assert 0 <= cached <= inp, f"{model}: cached rate above input rate"


def test_snapshot_has_a_date():
    assert len(AS_OF) == 10 and AS_OF[4] == "-" and AS_OF[7] == "-"


def test_prefix_match_prefers_longest():
    assert lookup("gpt-4o-2024-08-06").matched == "gpt-4o"
    assert lookup("gpt-4o-mini-2024").matched == "gpt-4o-mini"
    assert lookup("claude-sonnet-4-5-20250929").matched == "claude-sonnet-4"


def test_unknown_model_is_flagged():
    p = lookup("some-model-that-does-not-exist")
    assert p.estimated is True


def test_known_model_is_not_flagged():
    assert lookup("gpt-4.1").estimated is False


def test_price_file_merges_over_table(tmp_path):
    f = tmp_path / "prices.json"
    f.write_text(
        json.dumps(
            {
                "_note": "mine",
                "gpt-4o": [1.0, 2.0, 0.5],
                "my-model": {"input": 3, "output": 6},
            }
        )
    )
    tbl = load_prices(str(f))
    assert tbl["gpt-4o"] == (1.0, 2.0, 0.5)
    assert tbl["my-model"] == (3.0, 6.0, 0.0)
    assert "_note" not in tbl
    assert "o3" in tbl  # untouched built-ins survive


def test_inline_override_parses():
    model, price = parse_override("my-model=1,4,0.1")
    assert model == "my-model"
    assert price.input == 1.0 and price.output == 4.0 and price.cached == 0.1


def test_replay_ratio_does_not_depend_on_prices():
    """The headline metric must survive a wrong price table."""
    from ctxburn.analyze import analyze
    from ctxburn.model import Call, Segment, Trace

    t = Trace(name="p")
    convo = [Segment("user", "hi")]
    for _ in range(3):
        t.add(Call(model="gpt-4o", segments=[Segment("system", "x" * 400), *convo],
                   input_tokens=200, output_tokens=10))
        convo.append(Segment("assistant", "ok"))
    cheap = analyze(t, table={"gpt-4o": (0.01, 0.02, 0.001)})
    dear = analyze(t, table={"gpt-4o": (100.0, 200.0, 10.0)})
    assert abs(cheap.replay_ratio - dear.replay_ratio) < 1e-9
    assert dear.total_cost > cheap.total_cost * 1000
