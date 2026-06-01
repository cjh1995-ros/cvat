"""Before/after overlay rendering for eyeball QA. Original (as-drawn) line is shown
thin/dashed; the corrected line thick, color-coded by outcome state."""

from __future__ import annotations

import cv2
import numpy as np

from .edge_correct import (
    STATE_ALIGNED,
    STATE_AMBIGUOUS,
    STATE_CORRECTED,
    STATE_NO_EDGE,
    STATE_UNSUPPORTED,
    LineResult,
)

# BGR colors per outcome state
STATE_COLOR = {
    STATE_CORRECTED: (0, 220, 0),     # green  - moved onto an edge
    STATE_ALIGNED: (255, 160, 0),     # blue   - already on the edge
    STATE_NO_EDGE: (0, 165, 255),     # orange - no edge to snap to
    STATE_AMBIGUOUS: (255, 0, 255),   # magenta- competing parallel edges
    STATE_UNSUPPORTED: (128, 128, 128),  # gray
}
DRAWN_COLOR = (60, 60, 60)            # the original hand-drawn line (dark gray)


def _line(img, p, q, color, thickness):
    cv2.line(img, (int(round(p[0])), int(round(p[1]))),
             (int(round(q[0])), int(round(q[1]))), color, thickness, cv2.LINE_AA)


def render(image_bgr: np.ndarray, results: list[LineResult], scale: int = 2) -> np.ndarray:
    """Returns an upscaled overlay so sub-pixel shifts are visible."""
    canvas = cv2.resize(image_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    for r in results:
        pin = [v * scale for v in r.points_in]
        pout = [v * scale for v in r.points_out]
        color = STATE_COLOR.get(r.state, (200, 200, 200))
        # original drawn line, thin
        _line(canvas, pin[0:2], pin[2:4], DRAWN_COLOR, 1)
        # corrected (or kept) line, thick, color-coded
        _line(canvas, pout[0:2], pout[2:4], color, 2)
    _legend(canvas)
    return canvas


def _legend(canvas: np.ndarray) -> None:
    y = 16
    for state, color in STATE_COLOR.items():
        cv2.putText(canvas, state, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)
        y += 16
