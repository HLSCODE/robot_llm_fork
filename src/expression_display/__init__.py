"""
Optional T5L DGUSII expression display integration.

The project should depend on this package-level facade, not on the copied
SDK internals. Serial connections are opened lazily when an expression is
actually switched.
"""

from .display import (
    ExpressionDisplay,
    ExpressionDisplaySettings,
    ExpressionDisplayUnavailable,
    close_expression_display,
    get_expression_display,
    switch_expression,
)
from .base import ExpressionDisplayBackend, ExpressionSpec

__all__ = [
    "ExpressionDisplayBackend",
    "ExpressionDisplay",
    "ExpressionDisplaySettings",
    "ExpressionDisplayUnavailable",
    "ExpressionSpec",
    "close_expression_display",
    "get_expression_display",
    "switch_expression",
]
