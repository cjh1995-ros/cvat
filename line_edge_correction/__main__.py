"""CLI driver: read a CVAT 1.1 export dir, edge-correct every polyline, write a
corrected export + per-line JSON log + before/after overlay PNGs.

  .venv/bin/python -m line_edge_correction \
      --input  ~/repositories/private_workspace/datasets/sample-00 \
      --output /tmp/sample-00-corrected

Every run stamps its exact params into the log's `policy` block, so any result is
traceable to the settings that produced it (guideline §7: outcomes depend heavily
on policy). Any Policy field can be overridden with `--set field=value` (repeatable);
the most-used ones also have named flags. Example:

  ... --search-k 8 --set min_grad_frac=0.15 --set ambig_frac=0.5 --set boundary_margin=2

================================ PARAMETER REFERENCE ============================
Core philosophy is CONSERVATIVE NO-SNAP: when uncertain, leave the line as drawn.
Most knobs are levers on "how easily a snap is allowed vs. refused". Defaults in
parentheses; (flag) = has a named flag, else use --set.

-- (1) Normal search ---------------------------------------------------------
  search_k        (6.0, --search-k)  half-window searched along the line normal, px.
                  ↑ catches lines drawn farther off the edge, but risks grabbing a
                  NEIGHBORING parallel line. MUST stay < (parallel-line spacing)/2 —
                  critical for corrugated/tile grids. Hand-drawn GT often wants ~10.
  boundary_margin (1.0, --set)       reject a sample whose peak sits within this many
                  px of ±k (true edge may be OUTSIDE the window, or it's a neighbor).
                  ↑ blocks boundary jumps harder (→ more abstain_no_edge).
  search_step     (0.25, --set)      sub-pixel step along the normal. ↓ finer & slower;
                  little accuracy effect (a parabola refine already sub-pixels the peak).
  sample_spacing  (2.0, --set)       spacing of profile samples ALONG the segment, px.
                  ↓ more samples → steadier fit, slower.
  min_samples     (5, --set)         always take at least this many samples (short lines).

-- (2) Gradient gating (is there an edge to snap to?) ------------------------
  grad_blur_sigma   (1.0, --set)             Gaussian pre-smoothing before gradients.
                    ↑ ignores speckle/noise (helps faint ceilings); too high smears
                    close parallel lines together.
  grad_dir_tol_deg  (30, --grad-dir-tol)     accept an edge only if its gradient is
                    within this angle of the segment normal (i.e. edge ~⊥ to the line).
                    ↓ stricter (rejects oblique edges → more abstain). ↑ looser.
  min_grad_frac     (0.10, --min-grad-frac)  peak must exceed this fraction of the
                    image's robust-max gradient (99th pct) to count as an edge
                    (contrast-adaptive). ↑ rejects weak edges → more abstain_no_edge.
                    Key lever for faint ceilings (§6).

-- (3) Ambiguity (competing parallel edges → abstain_ambiguous) --------------
  ambig_ratio   (0.70, --set)  a 2nd peak >= this * primary marks the sample ambiguous.
                ↓ flags ambiguity more readily → more abstains.
  ambig_min_sep (2.0, --set)   the 2nd peak must be >= this many px from the primary to
                count as a competing parallel edge (adjacent pixels = same edge).
  ambig_frac    (0.40, --set)  abstain the whole line if >= this fraction of samples are
                ambiguous. ↑ pushes through ambiguity (more aggressive).

-- (4) Robust fit (RANSAC + total-least-squares) -----------------------------
  ransac_thresh   (1.5, --ransac-thresh)  inlier distance to the fitted line, px.
                  ↑ looser (may chase curves/noise), ↓ stricter.
  ransac_iters    (100, --set)            candidate point-pairs tried (deterministic,
                  reproducible). Little accuracy effect.
  min_valid_frac  (0.50, --set)  abstain_no_edge if fewer than this fraction of samples
                  found an edge. ↑ "only correct clearly-visible lines".
  min_inlier_frac (0.50, --set)  abstain if fewer than this fraction of valid samples are
                  RANSAC inliers. ↑ "only correct clean straight edges".

-- (5) Classification / conservative caps ------------------------------------
  aligned_eps  (0.5, --set)        endpoint move <= this → classified already_aligned
               (edge found but not moved), not corrected.
  max_shift    (6.0, --max-shift)  abstain if an endpoint would move more than this; a
               big jump is likelier a wrong edge than a real correction. Usually raised
               together with search_k.

-- §5 length filter (not a Policy field) -------------------------------------
  --min-length (0 = off)  flag (never delete) segments shorter than this many px at the
               native resolution. 30 @ 640×480 per the guideline.

TUNING DIRECTION SUMMARY
  More conservative (more abstains): min_grad_frac↑, min_valid_frac↑, min_inlier_frac↑,
      ambig_frac↓, grad_dir_tol_deg↓, boundary_margin↑.
  More aggressive (more corrections): the opposite, plus search_k↑ / max_shift↑.
  Worried about grid over-snap: keep search_k < (parallel spacing)/2 and gate with ambig_*.
(See also SplitPolicy in junction_split.py for the junction-split stage.)
================================================================================
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

import cv2

from . import cvat_io
from .edge_correct import GradientField, Policy, correct_segment
from .overlay import render


def _find_image(images_root: str, subset: str, name: str) -> str | None:
    for cand in (
        os.path.join(images_root, subset, name),
        os.path.join(images_root, name),
    ):
        if os.path.isfile(cand):
            return cand
    # last resort: recursive search by basename
    for dirpath, _dirs, files in os.walk(images_root):
        if name in files:
            return os.path.join(dirpath, name)
    return None


def build_policy(args: argparse.Namespace) -> Policy:
    pol = Policy()
    # named convenience flags
    for f in ("search_k", "ransac_thresh", "grad_dir_tol_deg", "min_grad_frac", "max_shift"):
        v = getattr(args, f, None)
        if v is not None and hasattr(pol, f):
            setattr(pol, f, v)
    # generic --set field=value (overrides any Policy field, typed by its current value)
    for item in (args.set or []):
        key, _, val = item.partition("=")
        key = key.strip()
        if not hasattr(pol, key):
            raise SystemExit(f"--set: unknown policy field '{key}'. Fields: {list(pol.to_dict())}")
        cur = getattr(pol, key)
        setattr(pol, key, type(cur)(val))
    return pol


def main() -> None:
    ap = argparse.ArgumentParser(description="Edge-correct CVAT 1.1 polyline GT (guideline §7).")
    ap.add_argument("--input", required=True, help="CVAT 1.1 export dir (has annotations.xml + images/)")
    ap.add_argument("--output", required=True, help="output dir for corrected export + logs + overlays")
    ap.add_argument("--search-k", dest="search_k", type=float, help="normal half-window, px (default 6)")
    ap.add_argument("--ransac-thresh", dest="ransac_thresh", type=float, help="RANSAC inlier dist, px (default 1.5)")
    ap.add_argument("--grad-dir-tol", dest="grad_dir_tol_deg", type=float, help="gradient/normal angle tol, deg (default 30)")
    ap.add_argument("--min-grad-frac", dest="min_grad_frac", type=float, help="edge gradient gate, frac of 99pct (default 0.1)")
    ap.add_argument("--max-shift", dest="max_shift", type=float, help="abstain if endpoint move exceeds this, px (default 6)")
    ap.add_argument("--min-length", dest="min_length", type=float, default=0.0,
                    help="drop/flag segments shorter than this (px @ native res); §5 filter, default off")
    ap.add_argument("--set", action="append", metavar="FIELD=VALUE",
                    help="override any Policy field, e.g. --set boundary_margin=2 --set ambig_frac=0.5 "
                         "(repeatable; see Policy in edge_correct.py for all fields)")
    ap.add_argument("--no-overlay", action="store_true", help="skip overlay PNG rendering")
    args = ap.parse_args()

    in_dir = os.path.expanduser(args.input)
    out_dir = os.path.expanduser(args.output)
    xml_in = os.path.join(in_dir, "annotations.xml")
    images_root = os.path.join(in_dir, "images")
    os.makedirs(out_dir, exist_ok=True)
    overlay_dir = os.path.join(out_dir, "overlays")
    if not args.no_overlay:
        os.makedirs(overlay_dir, exist_ok=True)

    pol = build_policy(args)
    doc = cvat_io.load(xml_in)

    log: dict = {"policy": pol.to_dict(), "min_length": args.min_length, "frames": []}
    totals: Counter = Counter()

    for frame in doc.frames:
        img_path = _find_image(images_root, frame.subset, frame.name)
        if img_path is None:
            print(f"[warn] image not found for frame '{frame.name}', skipping")
            continue
        bgr = cv2.imread(img_path, cv2.IMREAD_COLOR)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        grad = GradientField.from_image(gray, pol.grad_blur_sigma)

        frame_log = {"name": frame.name, "lines": []}
        results = []
        for pl in frame.polylines:
            res = correct_segment(pl.points, grad, pol)

            # §5 minimum-length filter (flag only; we never auto-delete GT)
            length = ((pl.points[2] - pl.points[0]) ** 2 + (pl.points[3] - pl.points[1]) ** 2) ** 0.5 \
                if len(pl.points) == 4 else 0.0
            too_short = args.min_length > 0 and length < args.min_length

            # write corrected coords back into the live XML node
            pl.elem.set("points", cvat_io.format_points(res.points_out))
            results.append(res)
            totals[res.state] += 1

            frame_log["lines"].append({
                "state": res.state,
                "source": pl.source,
                "length_px": round(length, 2),
                "too_short": too_short,
                "max_disp_px": round(res.max_disp, 3),
                "disp_endpoints_px": [round(d, 3) for d in res.disp_endpoints],
                "valid_frac": round(res.valid_frac, 3),
                "inlier_frac": round(res.inlier_frac, 3),
                "ambig_frac": round(res.ambig_frac, 3),
                "peak_grad": round(res.peak_grad, 3),
                "points_in": [round(v, 2) for v in res.points_in],
                "points_out": [round(v, 2) for v in res.points_out],
                "note": res.note,
            })

        log["frames"].append(frame_log)

        if not args.no_overlay:
            out_png = os.path.join(overlay_dir, f"{os.path.splitext(frame.name)[0]}_overlay.png")
            cv2.imwrite(out_png, render(bgr, results))

    # write corrected CVAT 1.1 export
    doc.write(os.path.join(out_dir, "annotations.xml"))
    log["totals"] = dict(totals)
    with open(os.path.join(out_dir, "edge_correction_log.json"), "w") as f:
        json.dump(log, f, indent=2)

    print("Outcome states:")
    for state, n in sorted(totals.items()):
        print(f"  {state:24} {n}")
    print(f"\nWrote: {out_dir}/annotations.xml, edge_correction_log.json"
          + ("" if args.no_overlay else ", overlays/"))


if __name__ == "__main__":
    main()
