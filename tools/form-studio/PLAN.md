# Form Studio — a Mac app: photo → house style → printable 3D

## Context
You want to turn everyday objects (e.g. a photo of a coffee mug) into one consistent, repeatable aesthetic, the matte olive-and-white clay style in `styles/clay-minimal/reference.png`, and then into a file for your multi-colour FDM printer. The earlier plan was a terminal CLI. You now want a **local app with a visual UI**:
- it opens from the **Dock** as a Mac app with its own window
- the AI steps can run **free on your Mac (local open-source models)** or through **paid cloud APIs**, switchable in Settings

The long-term goal (2D PNG/vector → 3D) stays on the roadmap.

Decisions so far: Apple Silicon Mac; India (Hunyuan3D licence OK); style only, no pot (the object stands on its own flat base); exports are STL plus a multi-colour **3MF** (Bambu Studio / OrcaSlicer).

## Architecture
```
Form Studio.app (Dock)
  └─ pywebview native window ──► http://127.0.0.1:8765
        Python backend (FastAPI + uvicorn), runs on your Mac
          ├─ REST API + live progress (Server-Sent Events)
          ├─ serves the built web UI (React)
          ├─ pipeline core: ingest → stylize → reconstruct → printify → export
          └─ engines (switchable per step)
               local: SDXL+IP-Adapter+ControlNet (MPS) · TripoSR / Hunyuan3D-2mini
               cloud: fal.ai (one API key covers both steps)
```
- **One process, one job at a time on the GPU.** A single worker queue loads models lazily and keeps them in memory, so later runs are fast.
- **Everything stays on your Mac.** Jobs live in `~/FormStudio/jobs/<id>/`, the same folder layout as before: `00_input`, `01_cutout`, `02_candidates/`, `03_confirmed`, `04_raw.glb`, `05_print/`, and `manifest.json` for reproducibility.
- **API keys** are stored in the macOS Keychain (`keyring`), never in files or git.

## Consistency (unchanged core idea)
1. **Image lock:** a versioned style spec (prompt, negative prompt, reference image, palette, seeds, model IDs).
2. **Geometry lock:** deterministic post-processing forces the style whatever the AI produced: voxel remesh, smoothing, removal of small details, a standard scale and base, and the palette colours.

## The app (screens)
1. **Library:** a grid of past objects (thumbnail + 3D), with "New object".
2. **New → Photo:** drag and drop a photo; the background-removed cutout is shown so you can check it.
3. **Style:** generates 4 candidates in the clay-minimal style with a live progress bar. Regenerate or change the seed, then **click one to confirm** (the approval step).
4. **3D:** a three.js viewer (orbit, the olive body on the white base, a printer-bed grid in mm). Sliders for height, base margin and thickness, smoothing, and the magnet pocket on/off and size. These re-run only printify (seconds, no AI). A printability panel shows the watertight check, the thinnest wall, and overhang warnings.
5. **Export:** download `body.stl`, `base.stl` and `model.3mf` (two parts on two filament slots), plus optional STEP. Buttons for **Open in Bambu Studio / OrcaSlicer** (`open -a`) and **Show in Finder**.
6. **Settings:** engine per step (Local / Cloud), the fal.ai API key (saved to Keychain, with a "Test" button), device (auto: mps/cpu), output folder, and SDXL-Turbo "fast mode" for 8 GB Macs.

Visual design: calm and minimal, matching your portfolio (Inter / Instrument Serif, an off-white `#f6f5f1` paper background, olive accent from the palette).

## Backend API (FastAPI)
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/styles` | list style presets |
| GET/POST | `/api/jobs` | list jobs; create a job (multipart photo upload + style) → runs ingest |
| GET/DELETE | `/api/jobs/{id}` | job state + manifest; delete |
| POST | `/api/jobs/{id}/stylize` | `{n, seed?, engine}` → queued task |
| POST | `/api/jobs/{id}/confirm` | `{candidate}` |
| POST | `/api/jobs/{id}/reconstruct` | `{engine}` → queued task (AI mesh) |
| POST | `/api/jobs/{id}/printify` | `{height_mm, base, smooth, magnet}` → fast, sync |
| POST | `/api/jobs/{id}/export` | `{formats}` |
| GET | `/api/jobs/{id}/files/{path}` | images, preview GLB, STL/3MF downloads |
| GET | `/api/jobs/{id}/events` | SSE: stage, progress %, logs, errors |
| GET/PUT | `/api/settings` · POST `/api/settings/test-key` | engines, device, key status |
| POST | `/api/jobs/{id}/open` | `{app: "bambu" \| "orca" \| "finder"}` |

## Engines (pluggable)
- `Stylizer` protocol: `LocalSDXL` (SDXL + IP-Adapter style ref + ControlNet-depth, fp16 on MPS; `--fast` = SDXL-Turbo), `FalStylizer` (e.g. FLUX Kontext on fal, with the reference image + prompt), `Stub` (for tests).
- `Reconstructor` protocol: `TripoSR` (local default on Mac), `Hunyuan3DMini` (local option), `FalReconstructor` (Hunyuan3D / TRELLIS on fal), `Stub`.
- Cloud engines need only a fal.ai key (pay per use, roughly $0.05–0.50 per object). Local engines cost $0.

## Files (`tools/form-studio/`)
- `PLAN.md`: updated to this plan.
- `backend/pyproject.toml`: package `formstudio`. Core: `fastapi uvicorn[standard] python-multipart sse-starlette pydantic pyyaml numpy pillow scipy trimesh manifold3d pymeshlab rembg keyring httpx fal-client pywebview`. Extras: `[local-ai]` torch/diffusers/transformers/accelerate + TripoSR, `[hunyuan]`, `[step]` cadquery-ocp.
- `backend/formstudio/`
  - `core/`: `style.py` (StyleSpec + hash), `job.py` (folders, manifest), `ingest.py`, `printify.py`, `export.py` (STL, hand-written 3MF with `Metadata/model_settings.config` extruder assignment, optional STEP)
  - `engines/`: `base.py` (protocols + registry), `local_sdxl.py`, `local_triposr.py`, `local_hunyuan.py`, `fal.py`, `stub.py`
  - `api/`: `app.py` (FastAPI app, static UI mount), `routes_jobs.py`, `routes_settings.py`, `worker.py` (single-GPU task queue + SSE event bus), `settings.py` (JSON config + Keychain)
  - `desktop.py`: starts uvicorn on a free port in a thread and opens the pywebview window; quitting stops the server
  - `cli.py`: a thin optional CLI over the same core (handy for batch runs)
- `backend/tests/`: printify/export unit tests (synthetic meshes) and API tests (FastAPI TestClient with Stub engines).
- `web/`: Vite + React + TypeScript, `three` + `@react-three/fiber` / `drei` for the viewer. Pages as above. `npm run build` outputs to `backend/formstudio/web_dist/`.
- `styles/clay-minimal/style.yaml` + `reference.png` (already in the repo).
- `scripts/setup-mac.sh`: one-time setup. It checks for/installs `python@3.11` and `node` via Homebrew, creates a venv, `pip install -e backend[local-ai]`, builds the web UI, then builds the app.
- `scripts/build-app.sh`: builds `~/Applications/Form Studio.app` (Info.plist, `.icns` icon, a launcher that runs the venv's `python -m formstudio.desktop`). It's a light wrapper, so no 5 GB torch bundle and updates are just `git pull` + rebuild of the UI.
- `README.md`: install (`./scripts/setup-mac.sh`), then open Form Studio from the Dock / Launchpad; how to add a fal key; how to add a new style.
- `.gitignore`: `jobs/`, `web/node_modules/`, `backend/formstudio/web_dist/`, the venv.

## What you need (Mac)
- **Free:** Homebrew, Python 3.11, Node (the setup script installs them), about 10 GB of disk for local models (downloaded on first local run), and OrcaSlicer or Bambu Studio.
- **Optional and paid:** a fal.ai account + API key, only if you switch a step to Cloud.
- 16 GB+ of memory is recommended for local style generation; on 8 GB, use fast mode or Cloud.

## Who builds it
I build and test everything in this cloud session (the UI, the API, the pipeline, the stub engines and the fal engine code), then push to `claude/inspiring-volta-4ljhbb`. Running the local models needs your Mac, so your first run of `setup-mac.sh` is the real-hardware test.

## Roadmap (not in scope now)
2D PNG/SVG → 3D (vectorise → extrude/emboss/relief, reusing printify/export); accent colours via per-triangle `paint_color` in 3MF; a style editor in the app for new presets; a case-study page on alwi.in.

## Verification
- **Here (no GPU):**
  - `pytest`: the printified mesh is watertight, the base sits flat at z=0, the height matches, the wall check passes, and the 3MF unzips with 2 parts and 2 extruder assignments. API flow with Stub engines: create → stylize → confirm → reconstruct → printify → export, with SSE events arriving.
  - Playwright on the built UI against the backend with Stub engines (Chromium is pre-installed): upload a photo, pick a candidate, the viewer renders, sliders rebuild, and downloads work. Screenshots for review.
- **On your Mac:**
  - Run `./scripts/setup-mac.sh` and open **Form Studio** from the Dock.
  - Run the mug photo through with Local engines; repeat with Cloud once a fal key is added.
  - Open `model.3mf` in Bambu Studio / OrcaSlicer: two parts on two filaments, no repair warnings. Then print.
