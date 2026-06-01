# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Format adapted from the vault's experiment `CLAUDE_TEMPLATE.md` (Purpose → Known quirks kept crisp and overwritten; Appendix is append-only). The vault-ingest/ownership machinery is intentionally omitted — this is a tooling fork, not an experiment repo.

## Purpose

[CVAT](https://github.com/cvat-ai/cvat) (Computer Vision Annotation Tool) is a self-hosted annotation platform — a monorepo with a Django REST backend, a React/TypeScript frontend (Yarn workspaces), and a Python SDK/CLI.

**This checkout is customized for ground-truth annotation in the `line-extraction-benchmark` project** — ceiling structural-line GT (polyline segments) for a line-extraction VSLAM benchmark on `dataset/fassto_up` (VGA 640×480). The annotation contract lives at `~/repositories/obsidian-note/note/Output/projects/line-extraction-benchmark/gt-annotation-guideline.html` (vault project page: `wiki/projects/line-extraction-benchmark.md`). Two pieces support that guideline's workflow: (1) **done** — an OpenCV *reference view* in the OpenCV tool's Image tab (CLAHE→Unsharp preset for judging faint lines per §6/§10, plus Sobel/Canny gradient previews); (2) **planned** — the guideline's mandatory **edge correction** (§7), built as offline post-processing that snaps drawn lines to the *original-image* gradient edge (not a live CVAT tool). See the Appendix for status.

## Environment

- **Backend / tests**: Python 3.10, Docker + Docker Compose. Frontend served by a prebuilt `cvat/ui` nginx image (see Known quirks).
- **Frontend dev**: Node 20, Yarn 4 via Corepack (**not** Yarn Classic).
- **SDK/CLI + custom scripts**: a local venv (`.venv/`) with `cvat-sdk`, `cvat-cli`, `numpy`, and `pylsd-nova`.

```bash
# frontend deps (repo root)
corepack enable && yarn install
# SDK/CLI + line-detector script deps
python3 -m venv .venv && .venv/bin/pip install -e ./cvat-sdk -e ./cvat-cli numpy pylsd-nova
```

## Common commands

```bash
# Frontend (repo root)
yarn run build:cvat-core         # build a workspace (also build:cvat-ui, :cvat-canvas, ...)
yarn start:cvat-ui               # dev server, hot-reload (needs a running backend) — fastest UI loop
yarn run type-check              # tsc against cvat-ui/tsconfig.json
yarn lint[:fix]                  # eslint .

# Run the stack
docker compose up -d             # full stack; UI at http://localhost:8080
docker exec -it cvat_server bash -ic 'python3 ~/manage.py createsuperuser'

# Backend unit tests (needs cvat_opa running + a built server image)
python manage.py test --settings cvat.settings.testing cvat/apps -v 2 [-k <pattern>]

# REST/SDK/CLI tests (auto-manages containers)
pytest ./tests/python [--rebuild|--start-services|--stop-services|-k <expr>]

# Python formatting
dev/format_python_code.sh        # black + isort (line length 100, py310)

# Run the line-segment auto-annotation locally (pylsd)
cvat-cli --server-host <host> --auth <u>:<p> task auto-annotate <task_id> \
    --function-file line_segment_automatic_annotation/line_segment_detector.py --clear-existing
```

## Repository layout

```
cvat/apps/                 # Django apps: engine (core), iam (OPA auth), dataset_manager, quality_control, ...
cvat/settings/             # split settings: base / development / production / testing
cvat/schema.yml            # drf-spectacular API schema — source of truth for the SDK
cvat-core/                 # client domain lib; ALL server interaction goes through here
cvat-core/src/opencv/      # client-side OpenCV.js filters (BaseImageFilter subclasses)
cvat-core/src/annotations-actions/   # BaseShapesAction / BaseCollectionAction framework
cvat-ui/                   # React + Redux + Antd app
cvat-ui/src/.../controls-side-bar/opencv-control.tsx   # the OpenCV tool toolbar (Drawing/Image/Tracking tabs)
cvat-ui/src/utils/opencv-wrapper/   # OpenCV.js loader + registered annotation actions
cvat-sdk/ cvat-cli/        # Python client (api_client layer is GENERATED from schema.yml) + CLI
line_segment_automatic_annotation/  # local pylsd auto-annotation script (custom)
```

## Conventions (load-bearing)

- **Backend**: authorization is enforced by **Open Policy Agent** — editing rules means changing both the app's `rules/*.rego` *and* its `permissions.py`. Long operations run on **RQ workers** (`cvat_worker_*`), not in-request. Changing serializers/views changes `cvat/schema.yml`; the generated `cvat_sdk/api_client` must be **regenerated** (`cvat-sdk/gen/generate.sh`), not hand-edited.
- **Frontend**: the UI never calls the API directly — it goes through `cvat-core`. JS/TS uses Airbnb style **with 4-space indentation**.
- **OpenCV "Image" filters**: implement in `cvat-core/src/opencv/<name>.ts` (extend `BaseImageFilter`, set `currentProcessedImage`, **`.delete()` every Mat/MatVector in `finally`** — WASM has no GC) → register a factory under `imgproc` in `opencv-interface.ts` → add an `ImageFilterAlias` (`cvat-ui/.../image-processing.tsx`) → add a toggle in `opencv-control.tsx`. The per-frame pipeline in `canvas2d/canvas-wrapper.tsx` applies enabled filters automatically. Filters not in `supportedImageFilters` are not persisted (no `toJSON`).
- **Annotation actions** (transform existing annotations): implement a `BaseShapesAction`/`BaseCollectionAction` and `core.actions.register(...)` it. It then **auto-appears** in the top-bar "Run actions" modal *and* the per-object "Run annotation action" context menu (gated by `isApplicableForObject`); the modal renders `parameters` (NUMBER/SELECT/CHECKBOX) as form controls. No custom button needed.
- **Process discipline**: user-facing changes need a `changelog.d/` fragment (scriv). Pinned `cvat/requirements/*.txt` are generated from `*.in` — edit the `.in`. Main/PR branch is `develop`.

## Known quirks

- **Plain `docker compose up` runs prebuilt images** (`cvat/ui`, `cvat/server`) with no `build:` section → `docker compose build cvat_ui` prints `No services to build` and local source never reaches the image. To rebuild the UI from source, add the dev override and use `--no-deps` to avoid touching the server:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.dev.yml build cvat_ui
  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --no-deps cvat_ui
  ```
  ⚠️ The UI webpack build is memory-hungry; `ResourceExhausted: cannot allocate memory` means raise Docker Desktop memory (Resources → Memory, 8GB+) and avoid building `ui` + `server` in parallel. For iterating, the host dev server (`yarn build:cvat-core` once + `yarn start:cvat-ui`) is far faster.
- **Bundled OpenCV is `cvat-ui/src/assets/opencv_4.8.0.js`** (a custom WASM build). `createCLAHE` is **not** exposed — use `new cv.CLAHE(...)`. To verify a function exists, load the file in Node and check `cv.<fn>` inside `Module.onRuntimeInitialized` (grep is useless — symbols live in the WASM binary).
- **`pylsd` 0.0.2 on PyPI is broken on Python 3** (`from lsd import lsd` in its `__init__.py`). Use **`pylsd-nova`** (`from pylsd.lsd import lsd`).
- The local checkout has **no `node_modules`** by default (the repo builds in Docker); standalone `tsc` can spot-check individual cvat-core files but a full type-check needs `yarn install`.

---

## Appendix: observations

> *Append-only.* New findings go in a new sub-section; don't rewrite existing ones.

### 2026-05-31 — OpenCV "Image" tab filters added

Added 5 client-side filters to the OpenCV tool's Image tab, modeled on the existing Histogram Equalization:
- **Grayscale, Gaussian blur, CLAHE, Canny edge, Sobel (ksize 3 and 5)** — `cvat-core/src/opencv/{grayscale,gaussian-blur,clahe,canny-edge,sobel}.ts`, registered in `opencv-interface.ts`, aliased in `cvat-ui/src/utils/image-processing.tsx`, toggled via `opencv-control.tsx` (`renderImageToolButton`). The Sobel factory takes a kernel size (`imgproc.sobel(3)` / `sobel(5)`) and the two sizes are separate toggles with distinct aliases (`SOBEL_3`/`SOBEL_5`).
- All run **in-browser (OpenCV.js)**, per frame; parameterless toggles with sensible defaults + a `configure()` hook for future sliders. Confirmed `GaussianBlur`/`Canny`/`CLAHE`/`Sobel`/`convertScaleAbs`/`addWeighted` exist in the bundled opencv.js by loading it in Node. CLAHE = guideline §6/§10 "CLAHE reference view"; Sobel renders |Gx|+|Gy| gradient magnitude — a preview of where the planned edge-correction has signal.

### 2026-05-31 — Unsharp mask + "Reference view" preset

- **Unsharp mask** (`cvat-core/src/opencv/unsharp-mask.ts`, `(1+amount)*src − amount*GaussianBlur(src)`) added as a toggle — completes the guideline's §10 CLAHE→Unsharp combo (filters chain in `canvas-wrapper.tsx`, so no combo logic needed).
- **"Reference view" preset button** (`renderReferenceViewButton` in `opencv-control.tsx`) toggles CLAHE + Unsharp together; active = both present. Because `ENABLE_IMAGE_FILTER` appends without dedup, the handler disables any existing CLAHE/Unsharp first, then re-enables in **CLAHE→Unsharp order**. Image tab now has 8 toggles + the preset.

### 2026-05-31 — pylsd line-segment auto-annotation script

`line_segment_automatic_annotation/line_segment_detector.py` — a cvat-cli auto-annotation function: PIL→grayscale→`pylsd.lsd`, emits each segment as a 2-point **polyline**. Factory `create(label_name="structural_line", min_length=0.0)`; LSD has no confidence so `conf_threshold` is ignored. Requires `pylsd-nova` + `numpy`.

### 2026-06-01 — Edge-correction dev fixture + fixed I/O format

- **User-flagged: CVAT export/import type is always "CVAT 1.1 for images."** Edge-correction post-processing reads & writes this format only. Schema seen in `sample-00/annotations.xml`: root `<annotations><version>1.1</version>`; per frame `<image id name width="640" height="480">` containing `<polyline label="line-segment" source="auto|manual" occluded="0" points="x1,y1;x2,y2" z_order="0">`. Points are `;`-separated `x,y` pairs (float, can be slightly negative / >W at borders — §4 free-hanging). Single label `line-segment`, type `polyline`.
- **Dev fixture**: `~/repositories/private_workspace/datasets/sample-00/` — task `sample-00`, 2 frames (`fassto-sample.png`, `hyoseong-sample.png`, both 640×480) with LSD `source="auto"` polylines. This is the working set for edge-correction (the full `fassto_up/images` = 3094 frames, too many for iteration).

### 2026-06-01 — Edge-correction + junction-derivation post-processing implemented

New package `line_edge_correction/` (offline, venv: numpy + scipy + opencv-python-headless). I/O is CVAT 1.1 for images throughout.

- **Stage 1 — edge correction** (`python -m line_edge_correction`, guideline §7): ASM normal search → sub-pixel parabola peak (gradient-direction gated) → RANSAC+TLS line fit → **perpendicular-only** endpoint reprojection. Conservative no-snap via gates; **boundary-peak guard** (`boundary_margin`) rejects peaks at ±k window edge (effective accept band = `search_k − boundary_margin` — keep margin ≪ k or almost everything abstains). Outcome states `corrected`/`already_aligned`/`abstained_no_edge`/`abstained_ambiguous`; per-line log records state + displacement + **source** + the full policy. Overlay = state-colored. All Policy fields tunable via `--set field=value` (full param reference is the `__main__.py` module docstring).
- **Stage 2 — junction DERIVATION** (`python -m line_edge_correction.junction_split`, §9): computes pairwise intersections + wireframe graph (junctions[] + sub-segment edges) into `junction_graph.json` + overlay (the **unmodified, continuous GT lines** each in a distinct golden-angle color, with the derived junction nodes dotted on top — GT is not split, so the overlay does not render sub-segments). `--images` lets it chain off a corrected XML whose dir has no images.
- **User-flagged decision (load-bearing): junction derivation NEVER modifies the GT.** Reasons: (1) a GT line's continuity/occlusion is 100% the annotator's call at draw time (§4) — the occluder stays whole, only the occluded line is cut; 2D geometry can't tell which crossing line is the occluder, so symmetric auto-split would wrongly break occluders; (2) LSD/ELSED don't split at crossings, so the classical-detector metric needs CONTINUOUS GT lines. Junctions are a *derived analysis artifact* only. The GT-splitting code is gated behind opt-in `--emit-split-xml` (an extra eyeball copy, not the GT). Edge correction → junction derivation is the order; junctions are computed from the corrected GT.
- **Empirical (sample-00 hand-drawn, 247 lines, source mix manual/semi-auto/auto):** with `k=8` defaults, `already_aligned` was 34/74 for LSD `auto` vs **1/75 for `manual`** — quantifies §7's premise that hand placement is systematically off the gradient edge (manual corrected median ~2.5px). Confirms editing-tool family matters: LSD already sub-pixel, hand-drawn needs correction.

## Appendix: follow-up analyses

Work is split into **CVAT-side** (must be in-tool because it informs what the human draws) and **post-processing** (operates on exported coords + original images — simpler: full numpy/scipy/OpenCV-python, no WASM/TS). Items #4, #7, #8, #11, #12 from the original brainstorm were dropped as not worth it.

**CVAT-side**: done (image filters + Unsharp + Reference-view preset — see Appendix: observations). The synced dual-pane (§10 ideal) was dropped; the CLAHE/Reference toggle is the accepted substitute.

**Post-processing (offline Python on exported annotations — primary work)**
1. **Edge correction** (§7, the guideline's "auto 후처리" arm — chosen over a live CVAT action: §7 says no manual redo, so live WYSIWYG isn't needed, and Python gets RANSAC/Devernay for free). ASM-style (Cootes profile-normal search): sample points along each drawn segment → search the **line-normal** for the gradient max (Devernay sub-pixel, on the **original** image, never a detector output) → robust line fit (RANSAC) → reproject endpoints **perpendicular-only** (don't slide along the line — endpoints are free-hanging at occlusions §4).
   - **Policy: conservative no-snap.** When uncertain, leave the line exactly as drawn (user preference). Levers that bound mis-snapping: small search window `k < (parallel-line spacing)/2` (critical for dense tile grids), gradient-direction filter (accept only edges ~perpendicular to the line), RANSAC, and a confidence gate.
   - **Diagnostics, not a single "failure rate"** (the rate conflates "auto can't" with "already good"). Classify each line into an **outcome state**: `corrected` / `already_aligned` (edge found, ~0 move) / `abstained_no_edge` (faint/textureless) / `abstained_ambiguous` (competing parallel edges). Log **per-line displacement (px)** — *collected and stored, not surfaced live*; the user hands the logs to the agent later (signal of annotation care, not a metric the annotator watches). Record the **gate thresholds/policy** with every run (outcomes depend heavily on policy).
   - **Output**: corrected GT + per-line log (state + displacement + policy params) + **before/after overlay images** (original vs corrected line on the original frame, color-coded by outcome state) for eyeball QA.
   - **I/O format (fixed): CVAT 1.1 for images** — input and output are always this format (user decision). The post-processing reads/writes the `annotations.xml` directly (parse `<image>`/`<polyline points="x1,y1;x2,y2">`), so no SDK round-trip is required (SDK re-upload stays an optional spot-check). Dev fixture: `~/repositories/private_workspace/datasets/sample-00/` (`annotations.xml` + `images/default/{fassto,hyoseong}-sample.png`, 640×480, label `line-segment` type polyline) — use this, **not** the 3094-frame `fassto_up/images`.
   - Data flow: read polylines from the CVAT-1.1 `annotations.xml` + load original images → correct/classify → write corrected `annotations.xml` (same format) + logs + overlays; optionally re-upload corrected shapes via SDK for a spot-check.
2. **Minimum-length filter** (§5) — drop/flag segments `< 30px @640×480` (resolution-aware); fold in as an option of the post-processing script.
3. *(optional)* **Automated consistency checks** (§8) — offline heuristics that flag suspicious frames (e.g., a tile-grid line with an unlabeled near-parallel neighbor).

## Appendix: pointers for the analyzing agent

- OpenCV.js API surface + the live/draw edge-following analogue: `cvat-core/src/opencv/intelligent-scissors.ts`, `opencv-interface.ts` (`contours.findContours`/`approxPoly` already exist and are reusable for any contour re-fit variant).
- Annotation-action template: `cvat-ui/src/utils/opencv-wrapper/annotations-actions/tracker-mil.ts` (a registered OpenCV-backed action) + `cvat-core/src/annotations-actions/base-shapes-action.ts` (the `run`/`applyFilter`/`call` contract; single-object vs frame-range paths).

## Authoring rules

1. *Purpose ~ Known quirks* reflect current state — **overwrite** on change, keep each section ≈30 lines; push overflow to the Appendix.
2. *Appendix* is append-only — new findings as new dated sub-sections; only fix typos/factual errors in existing ones.
3. User-observed findings get a `**User-flagged:**` prefix.
4. Numeric results always reference which sample/config produced them.
