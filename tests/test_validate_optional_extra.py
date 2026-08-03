from unittest.mock import patch

from scripts.validate_optional_extra import SMOKE_CHECKS, main


def test_main_dispatches_selected_extra() -> None:
    calls: list[str] = []

    with patch.dict(SMOKE_CHECKS, {"server": lambda: calls.append("server")}):
        result = main(["server"])

    assert result == 0
    assert calls == ["server"]
