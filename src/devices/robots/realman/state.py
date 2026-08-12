"""Pure helpers for interpreting RealMan controller state payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def realman_state_error_codes(state: Mapping[str, object]) -> tuple[int, ...]:
    """Return non-zero errors from current and legacy SDK state shapes."""
    candidates: list[object] = [
        state.get("error_code", 0),
        state.get("arm_err", 0),
        state.get("sys_err", 0),
    ]
    nested_errors = state.get("err")
    if isinstance(nested_errors, Mapping):
        raw_codes = nested_errors.get("err", ())
        if isinstance(raw_codes, Sequence) and not isinstance(
            raw_codes,
            (str, bytes),
        ):
            candidates.extend(raw_codes)

    errors: list[int] = []
    for candidate in candidates:
        try:
            code = int(str(candidate), 0)
        except (TypeError, ValueError):
            continue
        if code and code not in errors:
            errors.append(code)
    return tuple(errors)
