"""
Automatic-annotation function for CVAT that detects straight line segments
using the LSD (Line Segment Detector) algorithm from pylsd.

It is meant to be run locally through cvat-cli, e.g.:

cvat-cli --server-host <host> --auth <user>:<pass> task auto-annotate <task_id> \
        --function-file line_segment_automatic_annotation/line_segment_detector.py \
        --function-parameter label_name=str:structural_line \
        --function-parameter min_length=float:20 \
        --allow-unmatched-labels

The task must have a label of type "polyline" (named "structural_line" by default) for the
produced shapes to be attached to. Use --allow-unmatched-labels if you want CVAT
to skip shapes whose label is not present in the task instead of failing.


How an AA detection function is structured (see cvat_sdk.auto_annotation):
  * spec  -> DetectionFunctionSpec : the labels the function can produce.
  * detect(context, image) -> Sequence[DetectionAnnotation] : per-image inference.

cvat-cli treats a module that exposes a module-level ``create`` callable as a
*function factory*: it calls ``create(**function_parameters)`` and uses the
returned object as the function. That is why the parameters above map to
``create``'s arguments.
"""

from collections.abc import Sequence

import numpy as np
import PIL.Image
from pylsd.lsd import lsd

import cvat_sdk.auto_annotation as cvataa
import cvat_sdk.models as models

# A single label used for every detected segment. The id only has to be unique
# within this spec; CVAT matches the label to the task by *name*, not by id.
_LABEL_ID = 0


class _LineSegmentDetectionFunction:
    def __init__(self, *, label_name: str, min_length: float) -> None:
        self._label_name = label_name
        self._min_length = min_length

        self._spec = cvataa.DetectionFunctionSpec(
            labels=[
                cvataa.label_spec(label_name, _LABEL_ID, type="polyline"),
            ],
        )

    @property
    def spec(self) -> cvataa.DetectionFunctionSpec:
        return self._spec

    def detect(
        self,
        context: cvataa.DetectionFunctionContext,
        image: PIL.Image.Image,
    ) -> Sequence[cvataa.DetectionAnnotation]:
        # LSD operates on a single-channel (grayscale) intensity image.
        gray = np.asarray(image.convert("L"))

        # pylsd returns an (N, 5) array; each row is (x1, y1, x2, y2, width).
        segments = lsd(gray)

        shapes: list[cvataa.DetectionAnnotation] = []
        for x1, y1, x2, y2, _width in segments:
            if self._min_length > 0:
                length = float(np.hypot(x2 - x1, y2 - y1))
                if length < self._min_length:
                    continue

            shapes.append(
                cvataa.shape(
                    _LABEL_ID,
                    type="polyline",
                    points=[float(x1), float(y1), float(x2), float(y2)],
                )
            )

        return shapes


def create(label_name: str = "structural_line", min_length: float = 0.0) -> cvataa.DetectionFunction:
    """
    Build the line-segment detection function.

    Parameters (pass via ``--function-parameter NAME=TYPE:VALUE``):
      * ``label_name`` (str): name of the task label to attach segments to. Default "line".
      * ``min_length`` (float): drop segments shorter than this many pixels. Default 0 (keep all).
    """
    return _LineSegmentDetectionFunction(label_name=label_name, min_length=float(min_length))
