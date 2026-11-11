# Form Studio: handoff brief for the next AI assistant

> **Read this first.** It's a self-contained brief for an AI assistant (or developer) picking up Form Studio to
> improve the overall experience. It covers:
> - what the app is and who it's for
> - how it's built
> - how to run and test it
> - the rules not to break
> - the bugs already fixed, so they don't come back
> - a prioritised backlog with acceptance criteria
>
> `APP_OVERVIEW.md` has a more detailed, screen-by-screen description of the current behaviour.
>
> State as of **11 Nov 2026**, branch `claude/inspiring-volta-4ljhbb`, commit `0b1bbeb` or later.

---

## 0. TL;DR

- **What it does:** a Mac app that turns a **photo of a real object** into a **3D-printable miniature in one
  consistent house style**, and exports two-colour print files for an FDM multi-colour printer (Bambu AMS, Prusa MMU).
- **Pipeline:**
  ```
  photo → cut-out → restyled image options (pick one) → 3D mesh → printable parts → 3MF / STL / STEP
  ```
- **Stack:**
  - a native Swift window (WKWebView) that starts a **FastAPI** backend
  - a **React + TypeScript + three.js** UI
  - geometry done with manifold3d, trimesh and scikit-image
- **AI engines are switchable per step:**
  - **local:** free; SDXL and TripoSR on Apple Silicon
  - **cloud:** pay per use; OpenAI images, Meshy, fal.ai, Claude
  - **offline:** instant, no AI
- **Health:** 77 backend tests pass and the UI builds clean. The main flows are verified with Playwright.
  - Cloud APIs are only tested against fakes.
  - The 3MF has never been opened in a real slicer.
- **Your job:** improve the experience toward "perfection". See §9 for the backlog and §6 for rules you must keep.

---

## 1. The user and their setup

- A designer in **India** on an **Apple Silicon Mac**. Not a developer: they want a Dock app, not a terminal.
- They print on a **multi-colour FDM printer** and use **Bambu Studio** (OrcaSlicer and PrusaSlicer also supported).
- They want to **repeat one aesthetic** across many objects. Their reference look is **matte olive clay miniatures on
  a bone-white base** (`styles/clay-minimal/reference.png`). They also like pixel/voxel and soft-clay looks.
- Cost: local and free is preferred; **pay per use is fine**. They use their own **OpenAI** key for images and want
  **Meshy** for 3D. **Claude** is used for sculpting, naming and drafting styles.
- **Formats** they print: **Stand** (with base), **Magnet** (flat back plus a magnet pocket) and **Keychain** (flat back
  plus a ring loop).
- How they give feedback: screenshots and short messages, often with typos. They care a lot about **visual polish**
  and **clear hierarchy**, and about no cut-off text or dead ends.

---

## 2. Architecture

```
Form Studio.app  (mac/main.swift: AppKit + WKWebView, built by scripts/build-app.sh with swiftc, ad-hoc signed)
  ├─ runs:  <repo>/.venv/bin/python -P -m formstudio.desktop --serve --port N   (cwd = backend/)
  ├─ shows "Starting…" until GET /api/health answers, then loads http://127.0.0.1:N/
  ├─ native file picker, confirm()/alert() dialogs, downloads, menus, "Open Log" on failure
  └─ quits → stops the server

backend/formstudio/          FastAPI, Python ≥3.10
  api/app.py                 all REST routes + serves web_dist (SPA, hash routing)
  api/worker.py              ONE background worker thread (GPU work is serialised), Task + Bus (SSE fan-out)
  api/settings.py            settings.json + secrets in macOS Keychain (`keyring`, service "Form Studio")
  pipeline.py                the steps: create, stylize, confirm, reconstruct, printify, export (+ helpers)
  core/job.py                one folder per object + manifest.json; stages
  core/style.py              StyleSpec (YAML), CRUD, references, versions/hash
  core/colours.py            palette suggestion from pictures (no AI)
  core/ingest.py             background removal (rembg) → square cut-out on white + mask
  core/printify.py           geometry: voxelize, min feature, finishes, flat cut, base, magnet, keychain ring
  core/export.py             STL, two-colour 3MF (Bambu/Orca/Prusa metadata), optional STEP
  core/primitives.py         builds meshes from Claude Sculpt "recipes" (soft primitives)
  engines/                   stylizers + reconstructors (see §4)
  models.py                  local model downloads: progress/ETA, stop, resume, delete, start-over queue
  claude.py                  Claude helpers (naming, style drafts) with structured outputs
  cli.py / desktop.py        CLI (`formstudio-cli`) and app entry

web/src/                     React 18 + Vite 5 + TS 5, three.js via @react-three/fiber + drei
  App.tsx                    shell + hash router: #/  #/job/<id>  #/styles  #/styles/new  #/styles/<id>  #/settings
  Library.tsx                object grid + new object drop zone
  JobPage.tsx                4-step object page: Photo → Style → Shape → Print
  StylesPage.tsx             styles list, NewStyle page, StyleEditor
  SettingsPage.tsx           summary + side nav: Engines, Cloud accounts, Local AI, Printing, Storage
  Viewer.tsx                 3D viewer (lazy-loaded chunk)
  ui.tsx                     router hook, toasts, Progress, KindChip, EnginePicker, Slider, Switch
  api.ts                     typed API client + types
  styles.css                 the whole design system (single file, CSS variables)
```

`web` is built into `backend/formstudio/web_dist/`, which is **not committed**. `scripts/setup-mac.sh` runs
`npm ci && npm run build`.

### Data on disk
| What | Where |
|---|---|
| Objects | `~/FormStudio/jobs/<slug>-<hex>/` (`manifest.json`, `00_input.*`, `01_cutout.png` + `_mask`, `02_candidates/candidate_<n>.png`, `03_confirmed.png`, `04_raw.glb`, `05_print/`) |
| User styles | `~/FormStudio/styles/<id>/style.yaml` + reference pictures. Built-ins in repo `styles/*/` are read-only |
| Settings | `~/FormStudio/settings.json`. **Keys are never in files**: macOS Keychain, or memory if there's no Keychain |
| Log | `~/FormStudio/formstudio.log` |
| Models | `~/.cache/huggingface` (shared blob store!), `~/.u2net` (rembg), `~/FormStudio/vendor/TripoSR` |

`FORMSTUDIO_HOME` overrides `~/FormStudio`, which tests and Playwright runs use.

### Job stages and manifest
Stages: `new → cutout → stylized → confirmed → reconstructed → printified → exported`.

`job.reset_after(stage)` clears later outputs.

Manifest keys:
- `id`, `name`, `subject`
- `style {id, hash}`
- `stage`
- `candidates[]`, each `{n, file, seed, engine, style_id, style_name, created}`
- `confirmed {index, ...candidate}`
- `reconstruct {engine, seed, ...}`
- `print_params` (format plus geometry/base/magnet/flat_back/ring)
- `report` (printability)
- `exports[]`, `history[]`

Candidates **accumulate**. Picking one (`confirm`) or switching style keeps them. Only "Clear options" removes them all.

### Style YAML (abridged, see `styles/clay-minimal/style.yaml`)
- `id`, `name`, `version`, `description`, `reference(s)`
- the words:
  - `prompt` (SDXL, with `{subject}`)
  - `negative_prompt`
  - `edit_prompt` (instruction for cloud editors)
  - `sculpt_guide` (for Claude Sculpt)
- `seeds: [..]`
- `stylize {size, steps, guidance, ip_adapter_scale, controlnet_scale}`
- `palette {body {name, hex}, base {name, hex}}`
- `geometry {finish: soft|voxel|faceted, height_mm, voxel_mm, soften_mm, min_feature_mm, voxel_size_mm, facets, ...}`
- `base {...}`, `magnet {...}`
- `formats {stand, magnet, keychain}`: overrides deep-merged on top

Saving bumps `version`, and the `hash` is recorded in each job.

### API (all JSON unless noted; long work returns a Task and streams progress)
```
GET  /api/health
GET  /api/styles                      POST /api/styles {name, description?, based_on?, ...}
GET|PUT|DELETE /api/styles/{id}       POST /api/styles/{id}/duplicate
POST /api/styles/{id}/references      (multipart, field "image", several files; max 4 total)
POST /api/styles/{id}/references/{i}/first     DELETE /api/styles/{id}/references/{i}
GET  /api/styles/{id}/references/{i}  GET /api/styles/{id}/reference
POST /api/styles/suggest-colours      (multipart pictures, or ?style=id) → {body:{name,hex}, base:{name,hex}}
POST /api/styles/{id}/draft           (Claude writes words/colours/finish from pictures)
GET  /api/engines                     → {stylize: Engine[], reconstruct: Engine[], device}
GET|PUT /api/settings                 POST /api/settings/test-key?service=fal|anthropic|openai|meshy
GET  /api/models → {packs, queue}     POST /api/models/start-over
POST /api/models/{pack}/download[?fresh=1]   POST /api/models/{pack}/stop
DELETE /api/models/{pack}             DELETE /api/models        POST /api/models/reinstall-libraries
GET  /api/storage[?refresh=1]         POST /api/storage/open {id}
GET|POST /api/jobs                    GET|PATCH|DELETE /api/jobs/{id}
POST /api/jobs/{id}/stylize {engine?, seed?, n?, quality?}   (adds options; publishes state after each image)
POST /api/jobs/{id}/cancel            (stops the running step; finished options are kept)
POST /api/jobs/{id}/use-now {candidate, engine?}   (stops the rest, picks this option, builds the 3D shape)
DELETE /api/jobs/{id}/candidates/{i}  DELETE /api/jobs/{id}/candidates
POST /api/jobs/{id}/confirm {index}   POST /api/jobs/{id}/reconstruct {engine}
POST /api/jobs/{id}/style {style_id}  POST /api/jobs/{id}/suggest-name
POST /api/jobs/{id}/rebuild-recipe    POST /api/jobs/{id}/printify {overrides}
POST /api/jobs/{id}/reset-print       POST /api/jobs/{id}/export {formats}
GET  /api/jobs/{id}/files/{path}      POST /api/jobs/{id}/open   (Finder / slicer)
GET  /api/jobs/{id}/events            (SSE: {"type":"task",...} progress and {"type":"job", job} state)
GET|POST /api/bench {size, compare}   POST /api/bench/cancel     GET /api/bench/files/{report.md|options.png}
```
A task's public form: `{kind, status: queued|running|done|error|cancelled, progress, message, error, elapsed, eta_s,
cancelling}`.

### Speed machinery (Nov 2026)
- **Timings:** `core/timing.py` writes one JSON line per stage to `~/FormStudio/timings.jsonl`. Only the keys in
  `SAFE_KEYS` are kept, so there are never paths, names or keys.
  - Each object's manifest gets `timings` (cutout, stylize with `first_option`, reconstruct, printify, export).
  - `timing.estimate()` (median of recent runs) gives each task its `eta_s`.
- **Worker:** a priority queue, with background warm-ups at `BACKGROUND`. `cancel(job)` sets a flag that the next
  progress call turns into `Cancelled`; the SDXL step callback calls progress every step. `submit(..., replace=True)`
  stops the running task for that object and runs right after it ("Use this now").
- **Memory budget (`engines/local.py`):** models stay loaded while they fit in half the Mac's RAM (`FAMILY_GB` sizes);
  the least recently used are unloaded only when needed.
  - Setting `keep_loaded`: `auto` (the default) or `one` (the old way).
  - Out of memory → unload everything and retry once.
- **Warm-up:** `local.warm()` loads the default local engines (only if downloaded, only if they fit, only on 16 GB+ Macs) when the app opens, a
  photo is added, or an option is picked. It runs as low-priority worker work; setting `warm_up`.
- **Quality (`sdxl_quality`):**
  - `best`: 30 steps
  - `quick`: SDXL-Lightning 8-step LoRA loaded as a named adapter on the same pipeline; Euler "trailing", guidance 0,
    no negative prompt; switched on and off without reloading
  - `lightest`: SDXL-Turbo, which replaced the old `fast_mode` (migrated in `settings._migrate`)
  - Each option records its quality.
- **Printify cache:** keyed on the AI mesh file (path, mtime, size) and every setting each level uses: voxelize →
  thin parts → finish. Base, magnet, ring and format changes only redo the booleans. Cached arrays are read-only.
- **Shape cache:** `~/FormStudio/cache/shape/<sha>.glb`, keyed on the confirmed picture + mask + engine + seed +
  device/model settings, with a 2 GB cap. Not used for offline engines or Claude Sculpt.
- **Cloud:**
  - engines with `batch = True` (ChatGPT images) get all seeds in one request
  - engines with `parallel = N` (fal) run N requests side by side
  - OpenAI 429s: short rate limits are waited out (Retry-After). "Limit 0" (the free tier: no images allowed) and
    "no credit" are never retried; they explain the fix with links to OpenAI Billing and Limits.
- **Speed test (`core/bench.py`):** variants `before` (one model at a time, Best, no print cache), `best`, `quick`.
  - Each run: cut-out → first option → the rest → pick → 3D → print parts → 3MF → one slider change.
  - The first run of each variant is cold; median and p90 come from the warm runs.
  - Checks: watertight, height ±0.3 mm, 3MF parts on extruders 1 and 2.
  - Output: `report.md`, `report.json` and `options.png` in `~/FormStudio/bench/<date>/`. The test objects are
    flagged `bench` and deleted afterwards.
  - Run it from Settings → Speed test or with `formstudio-cli bench`.

---

## 3. How to run, build and test

```bash
# backend (from tools/form-studio/backend)
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,matting]"          # add ,local-ai for SDXL/TripoSR; ,step for STEP export
python -m pytest -q                      # 77 pass, 2 skipped (real-model smoke tests)
FORMSTUDIO_SMOKE=1 python -m pytest tests/test_local_engine_smoke.py   # real SDXL, needs ~12 GB of models

# web (from tools/form-studio/web)
npm ci && npm run build                  # tsc --noEmit + vite build → backend/formstudio/web_dist

# run the app without the Mac shell
cd backend && FORMSTUDIO_HOME=/tmp/fs python -m uvicorn formstudio.api.app:app --port 8765
# open http://localhost:8765/   (offline engines "Palette preview" + "Lathe/Extrude" need no models or keys)

# on the user's Mac
cd ~/Alwi.in && git pull && cd tools/form-studio && ./scripts/setup-mac.sh   # rebuilds venv, web, .app
```

**Testing conventions:**
- Cloud APIs are tested with `httpx.MockTransport` fakes that match the documented request shapes.
- Claude is tested with a fake client.
- UI is checked with Playwright scripts (Chromium):
  - desktop at 1320×1000 and phone at 390×844
  - assert **no element overflows** (`scrollWidth > clientWidth`) and **no page errors**
  - take screenshots and look at them
- **Every UI change should be checked at phone width as well as desktop.**

---

## 4. Engines

| Step | id | Kind | Notes |
|---|---|---|---|
| Restyle | `local-sdxl` | local | SDXL + IP-Adapter (style's first reference picture) + depth ControlNet. 12 GB. Quality: Best (30 steps, ~1–2 min/image on M-series), Quick (SDXL-Lightning 8 steps, +0.4 GB), Lightest (SDXL-Turbo) |
| Restyle | `openai-image` | cloud | OpenAI Images *edits*; default model `gpt-image-2.5-sunburst`; photo + style refs; transparent bg |
| Restyle | `fal-nano-banana` | cloud | fal.ai; photo + all refs |
| Restyle | `fal-kontext` | cloud | fal.ai FLUX Kontext; words only |
| Restyle | `preview` | offline | recolours the cut-out with the palette; no AI |
| 3D | `triposr` | local | single image → mesh, <1 min; code cloned to `~/FormStudio/vendor/TripoSR`; `torchmcubes` shimmed |
| 3D | `hunyuan-mini` | local | optional install (`--with-hunyuan`), 4 GB |
| 3D | `meshy` | cloud | Meshy image-to-3D (needs Meshy Pro for API) |
| 3D | `fal-trellis`, `fal-hunyuan` | cloud | fal.ai |
| 3D | `claude-sculpt` | cloud | Claude returns a `ShapeRecipe` (soft primitives) via structured output; saved to `04_recipe.json`; rebuild is free and deterministic |
| 3D | `lathe`, `extrude` | offline | from the silhouette: cups/vases, or flat art |

Engine contract (`engines/base.py`):
- `available(ctx) -> (ok, reason)`
- `stylize(StylizeInput, ctx, progress) -> list[{file, seed, ...}]`
- `reconstruct(ReconstructInput, ctx, progress) -> dict`, which writes a y-up GLB

Unavailable engines show `reason` in the UI, and Settings maps them to a "Connect …" action (`unlockFor` in
`SettingsPage.tsx`).

---

## 5. UI: current screens and design system

### Design system (in `styles.css`; reuse it, don't invent a second one)
- **Colours:** paper `#f6f5f1`, card `#fdfcf9`, ink `#1b1b18`, muted `#85837a`, line `#e4e1d8`, olive `#5e6b2e` (the
  accent), olive-soft `#e8ebdb`, plus warn and bad.
- **Type:** *Instrument Serif* for h1/h2, *Inter* for UI, *JetBrains Mono* for labels and chips (uppercase, letter-spaced).
- **Pill buttons:**
  - `.btn`, with `.primary` (ink), `.olive`, `.ghost`, `.danger` and `.sm` variants
  - chips: `.chip` with `.cloud` / `.offline` dot colours
- **Settings building blocks:** `.set-section`, `.set-group`, `.set-list`, `.set-row` (grouped list rows: title,
  description, control on the right).
- **Other components:** `.note.ok|bad|warn|info`, `.progress .bar` (supports `.indet`), `.seg` (segmented control),
  `.savebar` (sticky Save/Discard), `.lightbox`.
- **Breakpoints:** 980, 860 and 640 px. Rows collapse to one column on a phone.
- **Light theme only.** Dark mode is in the backlog.

### Screens
1. **Library `#/`:** object grid, plus a drop zone for a new object (photo → name, optional; Claude can name it →
   style → Start).
2. **Object `#/job/<id>`:** a left rail with four steps.
   - **Photo:** original + cut-out, style picker, name + Ask Claude, engine, "Generate N options" or "Skip: use photo
     as is".
   - **Style:**
     - generate bar (style, engine, how many 1–4, **Generate N more**)
     - option cards that accumulate, showing skeletons while generating and labelled with style · engine · seed
     - on each card: **Use this →**, Remove, and click to open a lightbox (←/→, Esc); the picked one shows a
       **Chosen** badge
     - Clear options
   - **Shape:** chosen image, 3D engine, Build / Rebuild, plus the Claude recipe actions.
   - **Print:**
     - three.js viewer
     - Format (Stand / Magnet / Keychain)
     - sliders (height, finish + softness / block size / facets, thinnest part, base, magnet, ring); each rebuilds
       in seconds without AI
     - printability panel
     - export 3MF (+STEP), Save, Open in slicer, Show in Finder
3. **Styles `#/styles`:** even cards (collage or colour block), Customize (built-in) or Edit/Delete (yours), + New
   style.
   - **New style `#/styles/new`:**
     - name, feel, up to 4 pictures (drop, click or paste; make first)
     - Start from (copies another style)
     - colours auto-suggested from the pictures
     - finish
     - optional Claude draft
     - Create (shows progress) → editor
   - **Editor:**
     - sections: Look / Words / Preview / Draft with Claude / Print colours / 3D shape
     - a sticky save bar; leaving with unsaved changes is guarded
     - built-ins are read-only, with "Customize this style"
4. **Settings `#/settings`:**
   - a summary strip (default restyle engine, default 3D engine, accounts connected) and a sticky side nav
   - **Engines:** radio lists; engines that need setting up fold away, with Connect buttons
   - **Cloud accounts:** expandable rows with status, key and options
   - **Local AI:** device, restyle quality, keep models loaded, background warm-up, model rows (one main action, "•••" for Download fresh / Delete), and a folded
     Troubleshooting group (Start over, Delete all, Reinstall libraries)
   - **Printing:** slicer, export formats
   - **Storage:** usage bar + folders with Show in Finder

---

## 6. Rules: don't break these

1. **Secrets:**
   - API keys live only in the macOS Keychain (or in memory).
   - Never write them to settings.json, logs, the manifest, URLs or commits.
   - Never send the user's personal details to third-party services.
2. **One worker:** all GPU and AI work goes through `api/worker.py` (`worker.submit`). Don't run models on request
   threads.
3. **Determinism:** the same photo + style version + seed + engine should reproduce the same result. Record what you
   used in the manifest.
4. **Watertight output:**
   - `printify` must always return a watertight, manifold mesh.
   - Use `to_trimesh(..., process=False)`; trimesh's merge broke watertightness on faceted meshes.
   - The faceted finish retries with more faces if decimation breaks the mesh.
5. **Python launch:**
   - The app runs `python -P -m formstudio.desktop` with `cwd=backend/`.
   - `-P` stops a stray `formstudio/` folder elsewhere from shadowing the package (that was a real crash).
   - Keep `scripts/cleanup_old_install.py` and the tidy step in `setup-mac.sh`.
6. **IP-Adapter pipeline:**
   - Do **not** enable attention slicing on the SDXL + IP-Adapter pipeline. It replaces the IP-Adapter attention
     processors and caused `'tuple' object has no attribute 'shape'`.
   - Styles with no reference picture must still pass a blank 224×224 white image at IP-Adapter scale 0. Otherwise
     the UNet raises "requires the keyword argument image_embeds".
7. **diffusers ≥0.42:** use `pipe.vae.enable_tiling()`, not `pipe.enable_vae_tiling()` (removed).
8. **Downloads:**
   - huggingface_hub uses a **shared blob store**: delete by following snapshot symlinks, and keep blobs that other
     repos still reference.
   - Progress takes the **furthest** tqdm bar per file, because xet reports through two bars.
   - Clear the xet chunk cache on delete.
9. **rembg models** live in a subfolder of `~/.u2net`; find them with `rglob("*.onnx")`.
10. **Cut-out self-heal:** `pipeline.ensure_cutout` recreates `01_cutout.png` if it's missing. Keep calling it before
    stylize, use-as-is and reconstruct.
11. **Keychain ring:** the ring attaches at the top point of a slice of the body at ring height (this fixed hollow
    mugs). Its thickness follows `flat_back.thickness_mm`.
12. **Style id `new`** is reserved because `#/styles/new` is a route.
13. **UI quality bar:**
    - no horizontal overflow or cut-off text at 390 px or 1320 px
    - every long task shows progress and can be cancelled or recovered
    - destructive actions confirm
    - errors say what to do next
14. **Don't recreate existing apps' branding.** Model and service names are fine as plain text.
15. **Speed changes must keep output identical or be opt-in:**
    - The printify cache is tested against an uncached run (same vertices).
    - Quick quality is a choice, never silently swapped in.
    - Measure with the speed test before and after, and keep `timings.jsonl` free of private data
      (`SAFE_KEYS` only).
16. **Style pictures pass on the look, not the objects:**
    - Local SDXL feeds the IP-Adapter only into SDXL's style block by default (InstantStyle):
      `ip_adapter_scale_config()` in `engines/local.py` gives `{"up": {"block_0": [0, s, 0]}}`, with
      `s = min(1, ip_adapter_scale / 0.6)`.
    - A style's `stylize.reference_mode: full` brings back every layer. That copies objects from the reference
      (e.g. the olive blobs from the cactus photo).
17. **Errors people can act on:**
    - Raise `EngineError(message, detail, links, steps)` (`engines/base.py`). The worker passes on `error_detail`,
      `error_links` and `error_steps`, and the step shows an `ErrorCard` with Try again / Use another engine.
    - A 429 that waiting can't fix ("Limit 0", "Request too large", quota codes) is never retried.
18. **3 views:**
    - View makers live in `engines/views.py` (`VIEWERS`) and write `03_views/{front,left,back}.png` (+ masks).
      Sheets are cut by `core/views.split_sheet`.
    - `ReconstructInput.views` reaches engines with `multi_view = True` (Meshy → `/multi-image-to-3d`; fal
      `trellis/multi`; fal `hunyuan3d/v2/multi-view`; local `hunyuan-mv`). The shape-cache key includes the views.
    - The local view maker (MV-Adapter, cloned to `~/FormStudio/vendor/MV-Adapter`) and local Hunyuan3D-2mv are
      written from their official examples but **not run here** (no GPU or memory for them). Verify on the Mac.
19. **Clean up:** `core/cleanup.py` must never touch objects' files, styles in use, or models the default engines
    need. Unused models are listed but not ticked.
20. **Blender clay finish:**
    - `core/blender.py` finds Blender (setting `blender_path`, `$FORMSTUDIO_BLENDER`, /Applications, PATH) and runs
      `core/blender_clay.py` inside it with `--factory-startup` (STL in/out, params JSON).
    - The script supports Blender 3.6–5.x (the STL operators were renamed in 4.2). It has only been run with a
      stand-in here (running a downloaded Blender wasn't allowed); verify on the Mac.
    - `printify` falls back to Soft with a warning, so a print is never blocked.
21. **Git:**
    - work on the feature branch
    - don't commit `web_dist`, `.venv` or models
    - keep commits descriptive

---

## 7. What's verified and what isn't

| Area | Status |
|---|---|
| Backend logic (printify, formats, finishes, 3MF, styles, candidates, models, storage, engine request shapes, timings, cancel / use-now, memory budget, caches, cloud batching, speed test) | ✅ 95 pytest tests |
| SDXL Best and **Quick (Lightning + IP-Adapter + ControlNet)**, switching both ways on one loaded pipeline | ✅ real models on CPU here (`FORMSTUDIO_SMOKE=1`) |
| Actual speed-up on the user's Mac | ⚠️ not measured yet: run Settings → Speed test (Compare on) |
| UI flows (object flow with offline engines, styles, new style, editor, options gallery, settings) | ✅ Playwright, desktop + phone |
| Local SDXL (Clay Minimal and Pixel Voxel) | ✅ real models on CPU here; reached generation on the user's Mac |
| Full SDXL → TripoSR → 3MF on the Mac | ⚠️ not yet confirmed end to end by the user |
| OpenAI / Meshy / fal.ai / Claude for real | ❌ only against fakes (no keys here) |
| 3MF opened in Bambu Studio / OrcaSlicer as 2 parts on 2 filaments | ❌ never checked |
| Mac-only bits (Show in Finder, Reinstall via Terminal, Open in slicer) | ⚠️ written for macOS, not runnable in CI |
| Model rows with real downloads in the new Settings design ("•••" menu) | ⚠️ logic unchanged, but the new layout wasn't seen with a downloaded model |

---

## 8. Known rough edges

1. Restyle quality varies by engine. Local SDXL is slow (1–2 min per image) and less faithful than cloud.
2. Built-in styles other than Clay Minimal have **no reference pictures**, so they're matched by words only.
3. TripoSR guesses the back from one image, and thin layered parts get merged by the minimum feature size.
4. Loading models into memory (after download) shows a message and elapsed time, but no percentage. Warm-up and
   keep-loaded mostly hide it after the first object.
5. No undo/history for print settings ("Reset to style" only).
6. No batch mode in the UI (the CLI has it).
7. Some library errors are still technical; they point to the log.
8. Setup needs Terminal (git + script). There's no signed `.dmg` or auto-update.
9. Light theme only; keyboard shortcuts are minimal.
10. `confirm()` / `alert()` dialogs are native browser ones. They're functional but not styled.

---

## 9. Backlog (prioritised, with acceptance criteria)

### P0: experience breakers
1. **First-run onboarding.**
   - A three-step welcome:
     1. choose **Free on this Mac** or **Cloud**
     2. download models (sizes and time shown), or paste keys
     3. **try the sample object** (bundled photo)
   - *Done when:* a new user reaches an exported 3MF without opening Settings by themselves, and no engine is ever
     shown as selectable when it can't run.
2. **One primary action per step.**
   - Use a consistent **Continue →** in the same place on every step, with the next step highlighted in the rail.
   - *Done when:* each step has exactly one primary (dark or olive) button, and secondary actions are ghost buttons.
3. **Recoverable errors.**
   - Every failed task card offers **Try again**, **Use another engine** (prefilled with a working one) and **Show
     details** (log excerpt).
   - *Done when:* no error leaves the user on a dead end.
4. **Real progress for every long task:** *mostly done.*
   - Done: elapsed time, "about N left" (from past runs or steps done), and Cancel / Stop on Photo, Style and Shape.
   - Left: a percentage while models load into memory.
5. **Validate the 3MF in Bambu Studio and OrcaSlicer.**
   - *Done when:* it opens as one object with two parts assigned to filaments 1 and 2, at the right size, with no
     repair prompt.
   - Fix `core/export.py` metadata if needed.

### P1: quality and consistency
6. **Curated reference pictures for every built-in style** (soft clay, pixel voxel, low poly).
7. **Style test bench:**
   - run a style on three bundled sample objects (mug, plant, shoe) and show a 3×N grid
   - cache the results per style version
8. **Compare mode in the Style step:** 2–4 options side by side at full size, keyboard pick (1–4, Enter).
9. **Print presets** ("Keychain 35 mm", "Fridge magnet 40 mm", "Desk figure 60 mm").
   - Show the bed outline, real dimensions, and estimated filament grams and print time.
10. **Undo/redo for print settings** and an **export history** per object.
11. **Keep detail for thin objects:** a toggle that lowers `min_feature_mm` and suggests a higher-detail 3D engine.
12. **Multi-view input:** an optional side or back photo (Meshy multi-image supports 1–4 views).
13. **Styled in-app dialogs** to replace `confirm()` and `alert()`, keeping keyboard support (Enter/Esc).

### P2: delight
14. **Series shelf:** all objects of one style in one 3D lineup, to check family consistency.
15. **Batch:** drop many photos, then restyle all, pick per object, and export one arranged build plate.
16. **Accent colour:** a third filament for painted regions, using 3MF paint data.
17. **Share card:** a render plus a spec card for the portfolio.
18. **A signed `.dmg` with auto-update** (no Terminal).
19. **2D → 3D track:** SVG/PNG logos into reliefs, extrusions and cookie cutters with the same finishes.
20. **Dark mode** (tokens already in CSS variables) and a **keyboard map** (⌘N, ⌘S, ⌘E, ←/→, Esc).

### Polish checklist (apply everywhere)
- [ ] Consistent words: *object, option, style, shape, print*. No internal ids (seed or engine ids) unless asked for.
- [ ] Empty states with an example and a one-click sample (Library, Styles, Style step).
- [ ] Every image zoomable; every long task cancellable; toasts never cover buttons; errors persist until dismissed.
- [ ] Accessibility: focus rings, labels, contrast, `prefers-reduced-motion` (already respected).
- [ ] The viewer stays smooth with 100k-face voxel meshes (simplify for display only).

---

## 10. Where to start (suggested order)

1. Read `web/src/styles.css` (the design tokens), then `JobPage.tsx` (the main flow), then `pipeline.py`.
2. Run the backend with `FORMSTUDIO_HOME` set to a temp folder. Create an object with **Palette preview + Lathe** to
   see the full flow with no models or keys.
3. Pick a P0 item. Change the backend with tests, then the UI. Check with Playwright at 1320 px and 390 px. Run
   `pytest` and `npm run build`.
4. Update `APP_OVERVIEW.md` when the behaviour changes.
