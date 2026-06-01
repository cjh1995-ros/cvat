"""
Junction DERIVATION for ceiling-line GT (guideline §9 "Phase 2 후처리 — line 교차 계산").

Given the (edge-corrected) line segments, compute their pairwise intersections and
the wireframe-style junction graph (junction nodes + the sub-segment edges between
consecutive junctions on each line). This is a DERIVED ANALYSIS artifact only:

  *** The GT line segments are NEVER modified. ***

Why not split the GT (decided with the user):
  * Continuity / occlusion of a GT line is 100% the annotator's call (§4) — the
    occluder stays whole, only the occluded line is cut, at annotation time. 2D
    geometry alone cannot tell which crossing line is the occluder, so a symmetric
    auto-split would wrongly break occluders.
  * Classical detectors (LSD/ELSED) do NOT split at crossings — comparing against
    them needs the GT lines kept CONTINUOUS. Only wireframe methods (L-CNN/HAWP)
    want a junction graph, and §9 derives that without touching the line GT.

So: junctions are computed from the GT, never written back into it. The graph
(junctions[] + edges[]) goes to junction_graph.json; the overlay visualizes it
(each derived sub-segment a distinct color so junctions show as color changes).
Use --emit-split-xml ONLY if you want a CVAT-viewable split copy to eyeball — it
is an extra artifact, not the GT.

Conservative geometry, mirroring edge-correction:
  * near-collinear segments (a long line drawn in pieces) do not form a junction
  * T-junctions / near-misses are joined only within a small tolerance
  * coincident junctions (3+ lines through ~one point) are clustered into one node

Run (GT untouched; emits graph + overlay):
  .venv/bin/python -m line_edge_correction.junction_split \
      --input  <dir-with-corrected-annotations.xml> \
      --images <original-images-dir> \
      --output /tmp/junctions
"""

from __future__ import annotations

import argparse
import colorsys
import copy
import json
import math
import os
from dataclasses import asdict, dataclass

import cv2
import numpy as np

from . import cvat_io


@dataclass
class SplitPolicy:
    extend_tol: float = 2.0       # a segment endpoint falling this many px short of
                                  # another segment still counts as touching (T-junction)
    merge_radius: float = 3.0     # cluster junctions within this radius into one node
    min_angle_deg: float = 10.0   # below this crossing angle the two segments are treated
                                  # as the same line (near-collinear) -> no junction
    split_margin: float = 1.0     # don't create a split within this many px of an existing
                                  # endpoint (avoids micro sub-segments; it's an endpoint node)
    min_subseg_len: float = 0.0   # flag sub-segments shorter than this (px); 0 = off (§5)

    def to_dict(self) -> dict:
        return asdict(self)


def _seg_intersection(a, b, c, d):
    """Intersection of infinite lines AB and CD. Returns (point, t_ab, u_cd) or None
    if (near-)parallel. t/u are the line parameters (0=start, 1=end)."""
    r = b - a
    s = d - c
    denom = r[0] * s[1] - r[1] * s[0]
    if abs(denom) < 1e-9:
        return None
    ca = c - a
    t = (ca[0] * s[1] - ca[1] * s[0]) / denom
    u = (ca[0] * r[1] - ca[1] * r[0]) / denom
    return a + t * r, t, u


def _angle_deg(a, b, c, d) -> float:
    r = b - a
    s = d - c
    cosang = abs(r @ s) / (np.hypot(*r) * np.hypot(*s) + 1e-12)
    return math.degrees(math.acos(min(1.0, cosang)))


def _cluster(points: list[np.ndarray], radius: float) -> list[np.ndarray]:
    """Greedy cluster centroids; returns a centroid per input point (index-aligned)."""
    centers: list[np.ndarray] = []
    members: list[list[np.ndarray]] = []
    assign: list[int] = []
    for p in points:
        hit = -1
        for ci, c in enumerate(centers):
            if np.hypot(*(p - c)) <= radius:
                hit = ci
                break
        if hit < 0:
            centers.append(p.copy())
            members.append([p])
            assign.append(len(centers) - 1)
        else:
            members[hit].append(p)
            centers[hit] = np.mean(members[hit], axis=0)
            assign.append(hit)
    return [centers[a] for a in assign]


@dataclass
class FrameSplit:
    name: str
    # per original polyline: list of sub-segment point-lists [[x1,y1,x2,y2], ...]
    subsegments: list[list[list[float]]]
    junctions: list[list[float]]          # ACTUAL crossing points only (degree >= 2)
    nodes: list[list[float]]              # all graph nodes (junctions + free-hanging endpoints)
    edges: list[list[int]]                # (node_i, node_j) into `nodes`


def split_frame(frame: cvat_io.Frame, pol: SplitPolicy) -> FrameSplit:
    segs = []  # (orig_index, A, B)
    for i, pl in enumerate(frame.polylines):
        if len(pl.points) != 4:
            segs.append((i, None, None))     # unsupported -> passed through unchanged
            continue
        a = np.array(pl.points[0:2], float)
        b = np.array(pl.points[2:4], float)
        segs.append((i, a, b))

    n = len(segs)
    split_ts: list[list[float]] = [[] for _ in range(n)]   # interior split params per seg
    raw_junctions: list[np.ndarray] = []                   # all accepted intersection pts

    for i in range(n):
        _, ai, bi = segs[i]
        if ai is None:
            continue
        li = np.hypot(*(bi - ai))
        for j in range(i + 1, n):
            _, aj, bj = segs[j]
            if aj is None:
                continue
            lj = np.hypot(*(bj - aj))
            res = _seg_intersection(ai, bi, aj, bj)
            if res is None:
                continue
            p, t, u = res
            ext_i = pol.extend_tol / li
            ext_j = pol.extend_tol / lj
            if not (-ext_i <= t <= 1 + ext_i and -ext_j <= u <= 1 + ext_j):
                continue
            if _angle_deg(ai, bi, aj, bj) < pol.min_angle_deg:
                continue                                   # near-collinear -> not a junction
            raw_junctions.append(p)
            # record an interior split on each segment (skip if it's at an endpoint)
            for idx, tt, ll in ((i, t, li), (j, u, lj)):
                if pol.split_margin / ll < tt < 1 - pol.split_margin / ll:
                    split_ts[idx].append(float(tt))

    # cluster coincident junctions so shared endpoints land on identical coords
    snapped = _cluster(raw_junctions, pol.merge_radius) if raw_junctions else []
    # map raw junction -> snapped coord by nearest (cluster preserves order)
    snap_lookup = {id(p): s for p, s in zip(raw_junctions, snapped)}

    def snap(pt: np.ndarray) -> np.ndarray:
        best, bestd = pt, pol.merge_radius
        for s in snapped:
            dd = np.hypot(*(pt - s))
            if dd <= bestd:
                best, bestd = s, dd
        return best

    # build sub-segments per original polyline
    subsegments: list[list[list[float]]] = []
    node_coords: list[np.ndarray] = []
    edges: list[list[int]] = []

    def node_id(pt: np.ndarray) -> int:
        for k, c in enumerate(node_coords):
            if np.hypot(*(pt - c)) <= 1e-6:
                return k
        node_coords.append(pt)
        return len(node_coords) - 1

    for i in range(n):
        _, a, b = segs[i]
        if a is None:
            subsegments.append([list(frame.polylines[i].points)])  # pass through
            continue
        ts = sorted(set(split_ts[i]))
        # endpoints: snap to a junction node if within merge_radius (clean T-junctions)
        pa = snap(a)
        pb = snap(b)
        pts = [pa] + [snap(a + t * (b - a)) for t in ts] + [pb]
        # dedupe consecutive coincident points
        clean = [pts[0]]
        for p in pts[1:]:
            if np.hypot(*(p - clean[-1])) > 1e-6:
                clean.append(p)
        subs = []
        for k in range(len(clean) - 1):
            p0, p1 = clean[k], clean[k + 1]
            subs.append([float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1])])
            edges.append([node_id(p0), node_id(p1)])
        subsegments.append(subs if subs else [list(frame.polylines[i].points)])

    # junctions = ACTUAL crossing points only (degree >= 2), i.e. the clustered
    # intersections — NOT free-hanging endpoints (§4). node_coords additionally holds
    # the degree-1 endpoints, which the graph needs as edge nodes but are not junctions.
    junctions: list[list[float]] = []
    for s in snapped:
        if not any(np.hypot(*(s - np.array(j))) <= 1e-6 for j in junctions):
            junctions.append([float(s[0]), float(s[1])])

    return FrameSplit(
        name=frame.name,
        subsegments=subsegments,
        junctions=junctions,
        nodes=[[float(c[0]), float(c[1])] for c in node_coords],
        edges=edges,
    )


def apply_split_to_xml(frame: cvat_io.Frame, fs: FrameSplit) -> None:
    """Replace each original polyline element with its sub-segment polylines."""
    for pl, subs in zip(list(frame.polylines), fs.subsegments):
        if len(subs) == 1:
            pl.elem.set("points", cvat_io.format_points(subs[0]))
            continue
        parent = frame.elem
        # insert clones right after the original, then remove the original
        idx = list(parent).index(pl.elem)
        for off, sp in enumerate(subs):
            clone = copy.deepcopy(pl.elem)
            clone.set("points", cvat_io.format_points(sp))
            parent.insert(idx + 1 + off, clone)
        parent.remove(pl.elem)


def _distinct_color(i: int) -> tuple[int, int, int]:
    """Deterministic, visually-distinct color per segment via golden-angle hue spread
    (random-looking but reproducible across runs)."""
    h = (i * 0.61803398875) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.85, 1.0)
    return int(b * 255), int(g * 255), int(r * 255)  # BGR


def render_junctions(image_bgr, polylines: list, fs: FrameSplit, scale: int = 2):
    """Show the GT and the derived junctions TOGETHER: each (continuous, unmodified) GT
    line in its own distinct color, with the computed junction nodes dotted on top.
    The GT is never split, so we draw the lines as-drawn — not sub-segments."""
    canvas = cv2.resize(image_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    for i, pl in enumerate(polylines):
        color = _distinct_color(i)
        pts = pl.points
        for k in range(0, len(pts) - 2, 2):     # connect consecutive vertices (2-pt = 1 seg)
            p = (int(round(pts[k] * scale)), int(round(pts[k + 1] * scale)))
            q = (int(round(pts[k + 2] * scale)), int(round(pts[k + 3] * scale)))
            cv2.line(canvas, p, q, color, 2, cv2.LINE_AA)
    for jx, jy in fs.junctions:
        c = (int(round(jx * scale)), int(round(jy * scale)))
        cv2.circle(canvas, c, 4, (255, 255, 255), -1, cv2.LINE_AA)   # white fill
        cv2.circle(canvas, c, 4, (0, 0, 255), 1, cv2.LINE_AA)        # red ring
    return canvas


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Derive the junction graph from GT polylines, GT untouched (guideline §9).")
    ap.add_argument("--input", required=True, help="CVAT 1.1 export dir (annotations.xml + images/)")
    ap.add_argument("--output", required=True, help="output dir for the junction graph + overlays")
    ap.add_argument("--images", help="images dir (default <input>/images); use when chaining off a "
                                     "corrected XML whose dir has no images")
    ap.add_argument("--extend-tol", dest="extend_tol", type=float)
    ap.add_argument("--merge-radius", dest="merge_radius", type=float)
    ap.add_argument("--min-angle", dest="min_angle_deg", type=float)
    ap.add_argument("--min-subseg-len", dest="min_subseg_len", type=float)
    ap.add_argument("--set", action="append", metavar="FIELD=VALUE",
                    help="override any SplitPolicy field (repeatable)")
    ap.add_argument("--emit-split-xml", action="store_true",
                    help="ALSO write a CVAT 1.1 XML with the GT lines split at junctions "
                         "(an extra eyeball artifact; the GT itself is never modified)")
    ap.add_argument("--no-overlay", action="store_true")
    args = ap.parse_args()

    in_dir = os.path.expanduser(args.input)
    out_dir = os.path.expanduser(args.output)
    images_root = os.path.expanduser(args.images) if args.images else os.path.join(in_dir, "images")
    os.makedirs(out_dir, exist_ok=True)
    overlay_dir = os.path.join(out_dir, "overlays")
    if not args.no_overlay:
        os.makedirs(overlay_dir, exist_ok=True)

    pol = SplitPolicy()
    for f in ("extend_tol", "merge_radius", "min_angle_deg", "min_subseg_len"):
        v = getattr(args, f, None)
        if v is not None:
            setattr(pol, f, v)
    for item in (args.set or []):
        key, _, val = item.partition("=")
        key = key.strip()
        if not hasattr(pol, key):
            raise SystemExit(f"--set: unknown field '{key}'. Fields: {list(pol.to_dict())}")
        setattr(pol, key, type(getattr(pol, key))(val))

    doc = cvat_io.load(os.path.join(in_dir, "annotations.xml"))
    graph = {"policy": pol.to_dict(), "frames": []}
    total_in = total_out = total_junc = 0

    for frame in doc.frames:
        fs = split_frame(frame, pol)
        n_in = len(frame.polylines)
        n_out = sum(len(s) for s in fs.subsegments)
        total_in += n_in
        total_out += n_out
        total_junc += len(fs.junctions)

        short = []
        if pol.min_subseg_len > 0:
            for subs in fs.subsegments:
                for sp in subs:
                    L = math.hypot(sp[2] - sp[0], sp[3] - sp[1])
                    if L < pol.min_subseg_len:
                        short.append([round(v, 2) for v in sp])

        if args.emit_split_xml:
            apply_split_to_xml(frame, fs)
        graph["frames"].append({
            "name": frame.name,
            "n_lines_in": n_in,
            "n_subsegments_out": n_out,
            "n_junctions": len(fs.junctions),
            "junctions": [[round(c[0], 2), round(c[1], 2)] for c in fs.junctions],
            "nodes": [[round(c[0], 2), round(c[1], 2)] for c in fs.nodes],
            "edges": fs.edges,
            "short_subsegments": short,
        })

        if not args.no_overlay:
            img_path = None
            for cand in (os.path.join(images_root, frame.subset, frame.name),
                         os.path.join(images_root, frame.name)):
                if os.path.isfile(cand):
                    img_path = cand
                    break
            if img_path:
                bgr = cv2.imread(img_path, cv2.IMREAD_COLOR)
                cv2.imwrite(os.path.join(overlay_dir, f"{os.path.splitext(frame.name)[0]}_junctions.png"),
                            render_junctions(bgr, frame.polylines, fs))

    with open(os.path.join(out_dir, "junction_graph.json"), "w") as f:
        json.dump(graph, f, indent=2)
    if args.emit_split_xml:
        doc.write(os.path.join(out_dir, "annotations.xml"))

    print(f"GT lines: {total_in}  (unchanged)  ->  derived sub-segments: {total_out}, junctions: {total_junc}")
    outs = "junction_graph.json"
    if args.emit_split_xml:
        outs += ", annotations.xml (split copy)"
    if not args.no_overlay:
        outs += ", overlays/"
    print(f"\nWrote: {outs}")


if __name__ == "__main__":
    main()
