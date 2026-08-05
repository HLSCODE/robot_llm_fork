"""Static registry for expression-display providers."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .provider import ExpressionDisplayProviderDefinition
from .t5l_dgusii.provider import T5L_DGUSII_DISPLAY_PROVIDER


EXPRESSION_DISPLAY_PROVIDERS: Mapping[
    str, ExpressionDisplayProviderDefinition
] = MappingProxyType({
    T5L_DGUSII_DISPLAY_PROVIDER.name: T5L_DGUSII_DISPLAY_PROVIDER,
})


def resolve_expression_display_provider(
    provider_name: str,
) -> ExpressionDisplayProviderDefinition:
    normalized = provider_name.strip().lower()
    try:
        return EXPRESSION_DISPLAY_PROVIDERS[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(EXPRESSION_DISPLAY_PROVIDERS))
        raise ValueError(
            f"unsupported expression display provider: {normalized}; "
            f"supported providers: {supported}"
        ) from exc


__all__ = [
    "EXPRESSION_DISPLAY_PROVIDERS",
    "resolve_expression_display_provider",
]
