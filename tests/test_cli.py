from bot.__main__ import _parse_grid, _parse_params


def test_parse_params() -> None:
    assert _parse_params(["avg_hold_bars=24", "seed=3", "mode=fast"]) == {
        "avg_hold_bars": 24,
        "seed": 3,
        "mode": "fast",
    }


def test_parse_grid() -> None:
    assert _parse_grid(["seed=0..3", "n=10,20.5"]) == {"seed": [0, 1, 2, 3], "n": [10, 20.5]}
