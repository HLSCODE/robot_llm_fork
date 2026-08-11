from __future__ import annotations

DRAG_CARD_RELATIVE_SCALE = 0.88
DRAG_CARD_MIN_SCALE = 0.65
DRAG_CARD_MAX_SCALE = 1.25
DRAG_CARD_OPACITY = 0.84
DRAG_SOURCE_OPACITY = 0.28
DRAG_PREVIEW_MAX_WIDTH = 224.0
DRAG_PREVIEW_MAX_HEIGHT = 144.0


def bounded_drag_preview_scale(width: float, height: float) -> float:
    """Scale an in-canvas thumbnail with the shared drag-preview policy."""
    if width <= 0.0 or height <= 0.0:
        raise ValueError("drag preview dimensions must be positive")
    return min(
        DRAG_CARD_RELATIVE_SCALE,
        DRAG_PREVIEW_MAX_WIDTH / width,
        DRAG_PREVIEW_MAX_HEIGHT / height,
    )
