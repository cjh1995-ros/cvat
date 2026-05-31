# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Format adapted from the vault's experiment `CLAUDE_TEMPLATE.md` (Purpose → Known quirks kept crisp and overwritten; Appendix is append-only). The vault-ingest/ownership machinery is intentionally omitted — this is a tooling fork, not an experiment repo.

## Purpose

[CVAT](https://github.com/cvat-ai/cvat) (Computer Vision Annotation Tool) is a self-hosted annotation platform — a monorepo with a Django REST backend, a React/TypeScript frontend (Yarn workspaces), and a Python SDK/CLI.

**This checkout is customized for ground-truth annotation in the `line-extraction-benchmark` project** — ceiling structural-line GT (polyline segments) for a line-extraction VSLAM benchmark on `dataset/fassto_up` (VGA 640×480). The annotation contract lives at `~/repositories/obsidian-note/note/Output/projects/line-extraction-benchmark/gt-annotation-guideline.html` (vault project page: `wiki/projects/line-extraction-benchmark.md`). The customizations exist to support that guideline's workflow: a CLAHE *reference view*, fast line drawing, and (planned) mandatory **edge correction** (snap drawn lines to the image gradient edge).

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

Added 4 client-side filters to the OpenCV tool's Image tab, modeled on the existing Histogram Equalization:
- **Grayscale, Gaussian blur, CLAHE, Canny edge** — `cvat-core/src/opencv/{grayscale,gaussian-blur,clahe,canny-edge}.ts`, registered in `opencv-interface.ts`, aliased in `cvat-ui/src/utils/image-processing.tsx`, toggled via `opencv-control.tsx` (`renderImageToolButton`).
- All run **in-browser (OpenCV.js)**, per frame; parameterless toggles with sensible defaults + a `configure()` hook for future sliders. Confirmed `GaussianBlur`/`Canny`/`CLAHE` exist in the bundled opencv.js by loading it in Node. CLAHE is for the guideline's §6/§10 "CLAHE reference view".

### 2026-05-31 — pylsd line-segment auto-annotation script

`line_segment_automatic_annotation/line_segment_detector.py` — a cvat-cli auto-annotation function: PIL→grayscale→`pylsd.lsd`, emits each segment as a 2-point **polyline**. Factory `create(label_name="structural_line", min_length=0.0)`; LSD has no confidence so `conf_threshold` is ignored. Requires `pylsd-nova` + `numpy`.

## Appendix: follow-up analyses

1. **Edge-correction polyline action** (planned, primary) — a `BaseShapesAction` implementing the guideline's mandatory §7 edge correction, ASM-style (Cootes profile-normal search): sample points along the drawn segment → search the **line-normal** direction for the gradient max (Devernay sub-pixel, on the **original** image, not a detector output) → robust line fit (RANSAC) → reproject endpoints **perpendicular-only** (don't slide along the line; endpoints are free-hanging at occlusions). Add a **confidence gate** to leave faint/textureless lines untouched. Output: per-line displacement (px) + faint-failure rate, for the pilot's §13 measurement (auto-postproc vs custom-live decision).
2. **Synced dual-display (original | CLAHE)** — the guideline's §10 ideal; a larger UI custom feature, separate from the filter toggle already added.

## Appendix: pointers for the analyzing agent

- OpenCV.js API surface + the live/draw edge-following analogue: `cvat-core/src/opencv/intelligent-scissors.ts`, `opencv-interface.ts` (`contours.findContours`/`approxPoly` already exist and are reusable for any contour re-fit variant).
- Annotation-action template: `cvat-ui/src/utils/opencv-wrapper/annotations-actions/tracker-mil.ts` (a registered OpenCV-backed action) + `cvat-core/src/annotations-actions/base-shapes-action.ts` (the `run`/`applyFilter`/`call` contract; single-object vs frame-range paths).

## Authoring rules

1. *Purpose ~ Known quirks* reflect current state — **overwrite** on change, keep each section ≈30 lines; push overflow to the Appendix.
2. *Appendix* is append-only — new findings as new dated sub-sections; only fix typos/factual errors in existing ones.
3. User-observed findings get a `**User-flagged:**` prefix.
4. Numeric results always reference which sample/config produced them.
