"""
Edge correction for ceiling-line GT (line-extraction-benchmark, guideline §7).

Snaps hand-drawn polyline segments onto the *original-image* gradient edge, with a
**conservative no-snap** policy: when the gradient evidence is weak or ambiguous, the
line is left exactly as drawn. Never snaps to a detector output — only to the raw
image gradient (so the benchmark stays detector-agnostic).

Algorithm (ASM / Cootes profile-normal search), per 2-point segment:
  1. sample points along the drawn segment
  2. at each sample, search the *line-normal* for the gradient-magnitude peak
     (sub-pixel parabola fit, gradient-direction gated so only ~perpendicular
     edges are accepted)
  3. robust line fit (RANSAC + total-least-squares) to the found edge points
  4. reproject the two endpoints **perpendicular-only** (slide along the line is
     forbidden — endpoints are free-hanging at occlusions, guideline §4)

Each line is classified into an outcome state and logged with its endpoint
displacement and the gate/policy params of the run. I/O is CVAT 1.1 for images.

Run:
  .venv/bin/python -m line_edge_correction \
      --input  ~/repositories/private_workspace/datasets/sample-00 \
      --output /tmp/sample-00-corrected
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.ndimage import map_coordinates

# ----------------------------------------------------------------------------- #
# Outcome states (guideline §7 diagnostics — NOT a single "failure rate")
# ----------------------------------------------------------------------------- #
STATE_CORRECTED = "corrected"               # edge found, line moved
STATE_ALIGNED = "already_aligned"           # edge found, ~0 move
STATE_NO_EDGE = "abstained_no_edge"         # faint / textureless -> keep as drawn
STATE_AMBIGUOUS = "abstained_ambiguous"     # competing parallel edges -> keep as drawn
STATE_UNSUPPORTED = "abstained_unsupported"  # geometry the corrector won't touch


@dataclass
class Policy:
    """Gate thresholds + search params. Logged verbatim with every run, because
    the outcome-state distribution depends heavily on these (guideline §7)."""

    # --- normal search ---
    search_k: float = 6.0          # half-window along the normal, px. MUST stay
                                   # < (parallel-line spacing)/2 for dense grids.
    boundary_margin: float = 1.0   # reject a sample whose peak sits within this many
                                   # px of ±k: the true edge may lie OUTSIDE the window
                                   # (or it's a neighbor parallel line being grabbed),
                                   # so the peak is untrustworthy -> don't snap to it.
    search_step: float = 0.25      # sub-pixel sampling step along the normal, px
    sample_spacing: float = 2.0    # spacing of profile samples along the segment, px
    min_samples: int = 5           # always take at least this many samples

    # --- gradient gating ---
    grad_blur_sigma: float = 1.0   # pre-smoothing of the image before gradients
    grad_dir_tol_deg: float = 30.0  # accept an edge only if its gradient is within
                                   # this angle of the segment normal
    min_grad_frac: float = 0.10    # peak must exceed this fraction of the image's
                                   # robust max gradient (99th pct) to count as "edge"

    # --- ambiguity (competing parallel edges) ---
    ambig_ratio: float = 0.70      # a 2nd peak >= this * primary, far enough away,
    ambig_min_sep: float = 2.0     # ...by at least this many px -> sample is ambiguous
    ambig_frac: float = 0.40       # abstain if >= this fraction of samples ambiguous

    # --- robust fit ---
    ransac_thresh: float = 1.5     # inlier residual threshold, px
    ransac_iters: int = 100
    min_valid_frac: float = 0.50   # >= this fraction of samples must find an edge
    min_inlier_frac: float = 0.50  # >= this fraction of valid samples must be inliers

    # --- conservative caps / classification ---
    aligned_eps: float = 0.5       # max endpoint move (px) to call it already_aligned
    max_shift: float = 6.0         # if an endpoint would move more than this -> abstain

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class LineResult:
    state: str
    points_in: list[float]              # [x1,y1,x2,y2] as drawn
    points_out: list[float]             # [x1,y1,x2,y2] after correction (== in if abstained)
    disp_endpoints: list[float] = field(default_factory=list)  # per-endpoint move, px
    max_disp: float = 0.0
    n_samples: int = 0
    valid_frac: float = 0.0
    inlier_frac: float = 0.0
    ambig_frac: float = 0.0
    peak_grad: float = 0.0              # median peak gradient (normalized 0..1)
    note: str = ""


# ----------------------------------------------------------------------------- #
# Gradient field
# ----------------------------------------------------------------------------- #
@dataclass
class GradientField:
    gx: np.ndarray
    gy: np.ndarray
    mag: np.ndarray         # normalized to a robust [0, ~1] scale
    scale: float            # the 99th-pct raw magnitude used for normalization

    @classmethod
    def from_image(cls, gray: np.ndarray, sigma: float) -> "GradientField":
        f = gray.astype(np.float32)
        if sigma > 0:
            ksize = max(3, int(2 * round(3 * sigma) + 1))
            f = cv2.GaussianBlur(f, (ksize, ksize), sigma)
        # Scharr is more rotationally symmetric than 3x3 Sobel -> better normal peaks.
        gx = cv2.Scharr(f, cv2.CV_32F, 1, 0)
        gy = cv2.Scharr(f, cv2.CV_32F, 0, 1)
        raw_mag = np.hypot(gx, gy)
        scale = float(np.percentile(raw_mag, 99.0)) or 1.0
        return cls(gx=gx, gy=gy, mag=raw_mag / scale, scale=scale)

    def sample(self, xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Bilinear sample of (gx, gy, mag) at sub-pixel (x, y) locations."""
        coords = np.vstack([ys, xs])  # map_coordinates wants (row=y, col=x)
        gx = map_coordinates(self.gx, coords, order=1, mode="nearest")
        gy = map_coordinates(self.gy, coords, order=1, mode="nearest")
        mag = map_coordinates(self.mag, coords, order=1, mode="nearest")
        return gx, gy, mag


# ----------------------------------------------------------------------------- #
# Core: correct one 2-point segment
# ----------------------------------------------------------------------------- #
def correct_segment(points: list[float], grad: GradientField, pol: Policy) -> LineResult:
    if len(points) != 4:
        return LineResult(
            state=STATE_UNSUPPORTED, points_in=points, points_out=list(points),
            note=f"polyline has {len(points) // 2} vertices; only 2-point segments are corrected",
        )

    p0 = np.array(points[0:2], dtype=np.float64)
    p1 = np.array(points[2:4], dtype=np.float64)
    seg = p1 - p0
    length = float(np.hypot(*seg))
    if length < 1e-6:
        return LineResult(STATE_UNSUPPORTED, points, list(points), note="degenerate (zero-length)")

    d = seg / length                       # along-segment unit vector
    n = np.array([-d[1], d[0]])            # left normal unit vector

    # --- sample points along the segment ---
    n_samp = max(pol.min_samples, int(length / pol.sample_spacing) + 1)
    ts = np.linspace(0.0, 1.0, n_samp)
    base = p0[None, :] + ts[:, None] * seg[None, :]    # (n_samp, 2) sample anchors

    # --- offsets along the normal, sub-pixel ---
    offs = np.arange(-pol.search_k, pol.search_k + 1e-9, pol.search_step)
    cos_tol = math.cos(math.radians(pol.grad_dir_tol_deg))

    cand_pts: list[np.ndarray] = []        # accepted edge points (sub-pixel)
    cand_w: list[float] = []               # weights (peak magnitude)
    peak_vals: list[float] = []
    n_ambiguous = 0
    n_boundary = 0                         # peaks rejected for sitting at the window edge

    for anchor in base:
        # profile coords along the normal through this anchor
        xs = anchor[0] + offs * n[0]
        ys = anchor[1] + offs * n[1]
        gx, gy, mag = grad.sample(xs, ys)

        # gradient-direction gate: keep only offsets whose gradient is ~|| to the normal
        gmag = np.hypot(gx, gy) + 1e-9
        align = np.abs((gx * n[0] + gy * n[1]) / gmag)   # |cos(angle(grad, n))|
        gated = np.where(align >= cos_tol, mag, 0.0)

        if not np.any(gated > 0):
            continue
        i_peak = int(np.argmax(gated))
        peak = float(gated[i_peak])
        if peak < pol.min_grad_frac:
            continue

        # boundary-peak guard: a peak at the window edge means the real edge may lie
        # beyond ±k (or it's a neighboring parallel line). Untrustworthy -> reject the
        # sample. If many samples hit the boundary the line abstains via valid_frac.
        if abs(offs[i_peak]) >= pol.search_k - pol.boundary_margin:
            n_boundary += 1
            continue

        # --- sub-pixel parabola refinement of the peak (Devernay-style) ---
        t_sub = offs[i_peak]
        if 0 < i_peak < len(offs) - 1:
            a, b, c = gated[i_peak - 1], gated[i_peak], gated[i_peak + 1]
            denom = (a - 2 * b + c)
            if abs(denom) > 1e-9:
                delta = 0.5 * (a - c) / denom            # in steps, within (-1, 1)
                t_sub = offs[i_peak] + delta * pol.search_step

        # --- ambiguity: a competing parallel edge far enough from the primary ---
        for j, val in enumerate(gated):
            if val >= pol.ambig_ratio * peak and abs(offs[j] - offs[i_peak]) >= pol.ambig_min_sep:
                n_ambiguous += 1
                break

        cand_pts.append(anchor + t_sub * n)
        cand_w.append(peak)
        peak_vals.append(peak)

    n_valid = len(cand_pts)
    valid_frac = n_valid / n_samp
    ambig_frac = n_ambiguous / n_samp
    med_peak = float(np.median(peak_vals)) if peak_vals else 0.0

    res = LineResult(
        state="", points_in=points, points_out=list(points),
        n_samples=n_samp, valid_frac=valid_frac, ambig_frac=ambig_frac, peak_grad=med_peak,
    )

    # --- conservative gates: abstain (keep as drawn) when uncertain ---
    if valid_frac < pol.min_valid_frac:
        res.state = STATE_NO_EDGE
        res.note = f"only {n_valid}/{n_samp} samples found an edge"
        if n_boundary:
            res.note += f" ({n_boundary} rejected at window boundary)"
        return res
    if ambig_frac >= pol.ambig_frac:
        res.state = STATE_AMBIGUOUS
        res.note = f"{n_ambiguous}/{n_samp} samples have a competing parallel edge"
        return res

    # --- robust line fit (RANSAC + TLS) ---
    pts = np.array(cand_pts)
    w = np.array(cand_w)
    fit = _ransac_line(pts, w, n, pol)
    if fit is None:
        res.state = STATE_NO_EDGE
        res.note = "RANSAC found no consistent edge line"
        return res
    q, u, inlier_frac = fit
    res.inlier_frac = inlier_frac
    if inlier_frac < pol.min_inlier_frac:
        res.state = STATE_AMBIGUOUS
        res.note = f"inlier fraction {inlier_frac:.2f} below gate"
        return res

    # --- reproject endpoints PERPENDICULAR-ONLY (move along drawn normal n) ---
    m = np.array([-u[1], u[0]])            # fitted-line normal
    mn = float(m @ n)
    if abs(mn) < 1e-3:                     # fitted line ~perpendicular to drawn normal: pathological
        res.state = STATE_AMBIGUOUS
        res.note = "fitted line orientation inconsistent with drawn segment"
        return res

    new_p0, a0 = _project_along(p0, n, q, m)
    new_p1, a1 = _project_along(p1, n, q, m)
    disp = [abs(a0), abs(a1)]
    max_disp = max(disp)
    res.disp_endpoints = disp
    res.max_disp = max_disp

    # conservative cap: a big jump is more likely a wrong edge than a real correction
    if max_disp > pol.max_shift:
        res.state = STATE_AMBIGUOUS
        res.note = f"endpoint move {max_disp:.2f}px exceeds max_shift cap"
        return res

    if max_disp <= pol.aligned_eps:
        res.state = STATE_ALIGNED
    else:
        res.state = STATE_CORRECTED
        res.points_out = [float(new_p0[0]), float(new_p0[1]), float(new_p1[0]), float(new_p1[1])]
    return res


def _project_along(p: np.ndarray, n: np.ndarray, q: np.ndarray, m: np.ndarray) -> tuple[np.ndarray, float]:
    """Move p along direction n until it lands on the line {x : m·(x-q)=0}.
    Returns (new_point, signed_distance_a). Pure perpendicular move w.r.t. the drawn
    segment, so the along-line coordinate of p is preserved (no sliding)."""
    a = float(m @ (q - p)) / float(m @ n)
    return p + a * n, a


def _ransac_line(pts: np.ndarray, w: np.ndarray, n_hint: np.ndarray, pol: Policy):
    """RANSAC over candidate edge points; refit inliers by total least squares.
    Deterministic (no RNG): enumerates all point pairs when few, else a strided
    subset, so results are reproducible across runs."""
    npts = len(pts)
    if npts < 2:
        return None

    # candidate pairs (deterministic). For small N enumerate all; cap the count.
    pairs = []
    for i in range(npts):
        for j in range(i + 1, npts):
            pairs.append((i, j))
    if len(pairs) > pol.ransac_iters:
        step = len(pairs) // pol.ransac_iters
        pairs = pairs[::step][: pol.ransac_iters]

    best_inliers = None
    best_count = -1
    for i, j in pairs:
        a, b = pts[i], pts[j]
        seg = b - a
        L = np.hypot(*seg)
        if L < 1e-6:
            continue
        u = seg / L
        m = np.array([-u[1], u[0]])
        resid = np.abs((pts - a) @ m)            # perpendicular distance to candidate line
        inliers = resid <= pol.ransac_thresh
        cnt = int(np.count_nonzero(inliers))
        if cnt > best_count:
            best_count = cnt
            best_inliers = inliers

    if best_inliers is None or best_count < 2:
        return None

    # total-least-squares refit on inliers (weighted centroid + PCA direction)
    ip = pts[best_inliers]
    iw = w[best_inliers]
    q = np.average(ip, axis=0, weights=iw)
    cov = (iw[:, None] * (ip - q)).T @ (ip - q)
    _, vecs = np.linalg.eigh(cov)
    u = vecs[:, -1]                              # principal axis = line direction
    u = u / (np.hypot(*u) + 1e-12)
    inlier_frac = best_count / npts
    return q, u, inlier_frac
