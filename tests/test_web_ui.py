from pathlib import Path

from deep_work_assistant.web_ui import UI_DIR, _to_dict, build_parser


def test_ui_assets_exist() -> None:
    for filename in ("index.html", "app.css", "app.js"):
        path = UI_DIR / filename
        assert path.exists(), f"missing UI asset: {path}"
        assert path.stat().st_size > 100


def test_parser_defaults_to_localhost() -> None:
    args = build_parser().parse_args(["--no-browser"])
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.no_browser is True


def test_to_dict_handles_nested_values() -> None:
    assert _to_dict({"path": Path("x"), "values": (1, 2)}) == {
        "path": "x",
        "values": [1, 2],
    }
