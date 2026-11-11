# Form Studio — app overview (current state, 11 Nov 2026)

This document records everything Form Studio does today, how each part works, what has been verified, where the rough
edges are, and a prioritised list of improvements. It's meant to be the starting point for a round of experience and
polish work.

---

## 1. What it is

A Mac app that turns a photo of any object into a **3D-printable miniature in one consistent house style**.

```
Photo ─► cut-out ─► restyled options (pick one) ─► 3D shape ─► print-ready parts ─► 3MF / STL / STEP
```

- **Two-colour printing:** the object prints in colour 1, the base in colour 2 (AMS/MMU friendly).
- **Formats:** **Stand**, **Magnet** (flat back + magnet pocket) or **Keychain** (flat back + key-ring loop).
- **Styles:** a style defines the *picture* (prompts, reference pictures, palette) **and** the *3D finish* (soft
  clay, pixel/voxel cubes, low-poly facets). So a series looks like one family.
- **Runs on your Mac.** AI can be local (free), cloud (pay per use) or offline (instant, no AI), switchable per step
  and per object.

---

## 2. Where things live

| What | Where |
|---|---|
| The app (Dock / Launchpad) | `~/Applications/Form Studio.app`, a native Swift window (`mac/main.swift`) |
| Code (updated with `git pull`) | `~/Alwi.in/tools/form-studio` (branch `claude/inspiring-volta-4ljhbb`) |
| Python environment | `~/Alwi.in/tools/form-studio/.venv` |
| Your objects | `~/FormStudio/jobs/<id>/`, one folder each, with `manifest.json` |
| Your styles | `~/FormStudio/styles/<id>/` (`style.yaml` + reference pictures) |
| Settings | `~/FormStudio/settings.json`. API keys are in the **macOS Keychain**, not here |
| Log | `~/FormStudio/formstudio.log` |
| Local AI models | `~/.cache/huggingface` (SDXL 12 GB, TripoSR 1.9 GB, SDXL-Turbo 6.5 GB, Hunyuan3D 4 GB) |
| Background remover model | `~/.u2net` (~170 MB) |
| TripoSR code | `~/FormStudio/vendor/TripoSR` (~64 MB) |

**Install / update:** `cd ~/Alwi.in && git pull`, then `cd tools/form-studio && ./scripts/setup-mac.sh`.
- `--reinstall-ai` reinstalls the AI libraries from scratch.
- `--cloud-only` skips local AI.
- `--with-hunyuan` adds Hunyuan3D.
- `--with-step` adds STEP export.

---

## 3. Architecture

```
Form Studio.app (Swift, AppKit + WebKit)
  ├─ starts Python server as a hidden child process:  python -P -m formstudio.desktop --serve --port N
  ├─ shows "Starting…" until /api/health answers, then the web UI in a WKWebView
  ├─ native file picker, confirm/alert dialogs, downloads (Save buttons), menus
  └─ on quit: stops the server; on failure: alert with log tail + "Open Log"

Python backend (FastAPI)  backend/formstudio/
  api/        REST API, single GPU worker queue, live progress via Server-Sent Events, settings + Keychain
  core/       style specs, job folders, background removal, printify (geometry), export (3MF/STL/STEP),
              primitives (Claude recipes), colours (palette from pictures)
  engines/    restyle + image→3D engines (local, cloud, offline)
  models.py   local model/engine downloads with progress, delete, start-over
  claude.py   Claude calls (structured outputs)

Web UI (React + TypeScript + three.js)  web/src/  → built into backend/formstudio/web_dist/
```

---

## 4. Screens and flows

### 4.1 Library (`#/`)
- **Grid of objects:** thumbnail, name, style, stage and when it was last changed.
- **"New object" drop zone:**
  - drop a photo, then "What is the object?" (left empty → Claude names it if an Anthropic key is set)
  - pick a style, then **Start**
  - names like "Pasted…", "Screenshot…" or "IMG_1234" are ignored
- **Styles chip:** links to the Styles page.

### 4.2 Object page (`#/job/<id>`): four steps
1. **Photo**
   - the original and the cut-out (background removed)
   - a style picker; switching clears later steps
   - the object name, with **Ask Claude**
   - an engine picker for restyling
   - **Generate N options** or **Skip: use photo as is** (for 2D art, logos, things already in style)
2. **Style**
   - a generate bar: style · engine · how many (1–4) · **Generate N more**
   - options **accumulate** across runs (numbered 1, 2, 3…, fresh seeds each run) and appear one by one as each
     finishes; you can mix engines and styles; each card is labelled with style, engine and seed
   - each option: **Use this →** (goes to Shape), **Remove**, click to view large (lightbox with ←/→, Esc,
     Use this); the picked one shows a **Chosen** badge
   - while options are still being made: **Use this now →** stops the rest and builds the 3D shape straight away,
     and **Stop** ends the run (the options made so far stay)
   - with the local SDXL engine, a **Quality** choice: Best · Quick · Lightest
   - the progress bar shows the time so far and "about N left", based on past runs on this Mac
   - each finished step in the left rail shows how long it took ("took 48 s", or "reused" for a 3D shape made
     earlier from the same picture)
   - **Clear options** (with a confirm); going back to pick another option keeps them all and only redoes the shape
3. **Shape**
   - the chosen image and a 3D engine picker
   - **Build 3D shape** / **Rebuild shape**
   - with Claude Sculpt: **Rebuild from Claude's recipe (free)** and **Ask Claude again**
4. **Print**
   - **3D viewer** (orbit, bed grid, the object's colours; looks down at flat formats); **Print / AI mesh** toggle
     - **X / Y / Z axis gizmo** (bottom right; click an axis to look along it)
     - view buttons **Front · Back · Left · Right · Top · Bottom · 3/4** and **Spin**; keys 1 / 3 / 7 / 9 / 0 and R
     - the camera can go under the bed (to see a magnet pocket); the bed grid hides when you look from below
   - **Colours:** Body (filament 1) and Base / back (filament 2) per object, from a palette of common filament colours
     or a Custom colour (named automatically); **From the picture**, **Swap**, **Style colours**. Changing a colour
     doesn't rebuild the shape; it clears exported 3MF/STEP so you never save old colours.
   - **Format:** Stand · Magnet · Keychain, each with its own defaults
   - **Shape controls:** height; **3D finish** (Soft / Pixel-voxel / Low-poly) with softness, block size or facet
     count; thinnest part; detail
   - **Format controls:**
     - base on/off, margin and thickness (stand)
     - thickness (magnet and keychain)
     - key-ring hole and loop thickness (keychain)
     - magnet Ø and depth
   - **Printability panel:** watertight, size, material, thinnest part, overhangs, warnings
   - **Export:** 3MF (two colours) and optional STEP; **Save** buttons; **Open in Bambu Studio/Orca/Prusa**;
     **Show in Finder**
   - every slider rebuilds in seconds (no AI)

### 4.3 Styles (`#/styles`, `#/styles/<id>`)
- **Cards:** equal heights, nothing cut off; thumbnail is a collage of the reference pictures or a two-colour block
  with the finish; colour dots; Built-in / Yours tag. **Customize** on built-ins (makes your copy), **Edit** /
  **Delete** on yours. A dashed **+ New style** card and a **New style** button.
- **New style (`#/styles/new`):**
  - name and a one-line "what should it feel like"
  - reference pictures (up to 4; drop, click or paste) **before** creating; **make first** / remove
  - **Start from** any existing style (copies its words, 3D finish and shape settings)
  - print colours, auto-suggested from the pictures (free, no AI), with **Suggest from pictures** to redo
  - 3D finish (Soft / Pixel-voxel / Low-poly) with its sliders
  - optional **Let Claude write the style words** (with an Anthropic key)
  - **Create style** shows progress (creating → uploading pictures → Claude) and opens the editor
- **Editor:** sections Look (pictures) · Words · Preview · Draft with Claude · Print colours · 3D shape; pictures
  save immediately; a sticky **Discard / Save** bar while there are changes (Save bumps the version); a warning if
  you leave with unsaved changes.
- Built-in styles open read-only with **Customize this style**.

### 4.4 Settings (`#/settings`)
- **Summary at the top:** the default restyle engine, the default 3D engine, and how many cloud accounts are connected.
  Click any of them to jump to its section.
- **Side list of sections** (it turns into a scrolling tab strip on narrow windows) that highlights where you are:
  - **Engines**
    - two radio lists, Restyle and 3D shape, showing the engines that are ready
    - the rest fold into "N more need setting up"; each has a **Connect …** button that opens the right account
      and focuses its key field
    - options per Generate
  - **Cloud accounts**
    - one row each for OpenAI, Meshy, Anthropic and fal.ai, showing what it powers and Connected / Not connected
    - click a row to expand it: key field (or Test key / Remove), then that service's options (model, quality,
      effort, auto-naming, endpoints)
  - **Local AI**
    - compute device; **Restyle quality** (Best · Quick · Lightest); **Keep models loaded** (Automatic · One at a
      time, showing the Mac's memory and what's loaded); **Get engines ready in the background**
    - models and engines (x of y ready, size on disk); each row has its status and one main action
      (Download / Resume / Stop), with Download fresh and Delete under a "•••" menu
    - a folded **Troubleshooting** group: Start over, Delete all, Reinstall AI libraries
  - **Speed test**
    - Quick check (1 photo × 2 runs) or Full (3 × 3), with **Compare** to also run the old way and Quick quality
    - the results table shows medians with speed-ups, a print-check line and a sheet of the options each way made,
      plus the full report
  - **Printing:** slicer, files to export
  - **Storage:** total use, a colour bar by folder, and every folder with its size, path and **Show in Finder**

---

## 5. Engines

| Step | Engine | Where | Cost | Notes |
|---|---|---|---|---|
| Restyle | SDXL + style reference | local | free | SDXL + IP-Adapter (style pictures) + depth ControlNet. ~1–2 min/image on Apple Silicon. 12 GB models |
| Restyle | ChatGPT images (OpenAI) | cloud | ~few ¢–20¢/image | `gpt-image-2.5-sunburst` default; photo + style references in one call; transparent background |
| Restyle | Nano Banana (fal.ai) | cloud | ~$0.04/image | Photo + all style references |
| Restyle | FLUX Kontext (fal.ai) | cloud | ~$0.025/image | Words only |
| Restyle | Palette preview | offline | free | Recolour only, no AI |
| 3D | TripoSR | local | free | Under a minute; code + 1.9 GB model |
| 3D | Hunyuan3D-2 mini | local | free | Optional install; 4 GB |
| 3D | Meshy | cloud | ~5–20 credits (~$0.10–0.40) | Needs Meshy **Pro** plan for API |
| 3D | TRELLIS (fal.ai) | cloud | ~$0.02 | Cheapest cloud 3D |
| 3D | Hunyuan3D-2 mini (fal.ai) | cloud | ~$0.10 | |
| 3D | Claude Sculpt | cloud | ~$0.02–0.06 | Claude writes a recipe of soft primitives; rebuild is free and identical |
| 3D | Lathe / Extrude | offline | free | From the silhouette: cups/vases, or flat art |
| Other | Claude naming, style drafts | cloud | ~1¢ | Anthropic key |

---

## 6. Styles and the 3D finish

Built-in styles:
- **Clay Minimal:** olive matte clay, soft finish, with a reference photo.
- **Soft Clay:** puffy pastel, extra soft.
- **Pixel Voxel:** cubes, voxel finish.
- **Low Poly:** facets, faceted finish.

The new built-ins have no reference pictures; their words alone set the look.

**Geometry lock (printify):** the same deterministic steps finish every AI mesh:
1. voxelize + fill
2. remove parts thinner than the minimum
3. finish: **soft** (blur + Taubin smoothing), **voxel** (cubes with a small bevel) or **faceted** (decimated to
   ~400 flat faces)
4. exact height, flat bottom, base / flat back, magnet pocket, key-ring loop
5. watertight check, overhang report

---

## 7. What's verified, and how

| Area | Verified |
|---|---|
| Backend logic | **73 automated tests** (pytest) with fake AI services: printify, formats, finishes, export 3MF, styles CRUD, engines' request shapes, downloads, start-over, errors |
| UI flows | Playwright runs (offline engines) for the object flow, styles, settings, models card; phone width checked |
| Local SDXL | Real models run here on CPU (same diffusers 0.42 / transformers 5 as your Mac): Clay Minimal and Pixel Voxel both generate. Opt-in smoke test `FORMSTUDIO_SMOKE=1` |
| Model downloads | Real TripoSR + background remover downloads, Start over, delete frees space |
| On your Mac | App opens (native shell), downloads work, SDXL generation reached; **not yet confirmed end to end**: a full SDXL → TripoSR → 3MF print |
| Cloud APIs | **Not called for real** (no keys here): OpenAI, Meshy, fal.ai and Claude are tested against fakes matching their documented APIs |
| Slicer import | 3MF two-part/two-filament layout written per Bambu/Orca format; **not opened in a real slicer yet** |

---

## 8. Known rough edges (honest list)

1. **Restyle quality depends on the engine.** Local SDXL at the Mac's default 1024 px / 30 steps is decent but slow.
   Cloud engines are faster and usually closer to the style.
2. **Built-in styles other than Clay Minimal have no reference pictures,** so SDXL and Nano Banana match them by
   words only.
3. **TripoSR guesses the back of an object from one image.** Thin parts such as a tree's layered fins get thickened
   or merged by printify's minimum feature size.
4. **First-run waits:** loading the models into memory takes a while even after download, with no detailed progress
   for that part.
5. **No undo / history** for print settings; "Reset to style" is the only way back.
6. **No batch mode in the UI.** The CLI (`formstudio-cli run`) can batch.
7. **The 3MF hasn't been checked in Bambu Studio / OrcaSlicer yet.**
8. **Error messages are better but still technical** for library errors; they point to the log.
9. **Setup needs Terminal** (git, setup script). There's no packaged installer or auto-update.

---

## 9. Recently finished

- **Speed:**
  - models stay loaded while they fit, and get ready in the background
  - **Use this now** while options are generating, and Cancel / Stop everywhere
  - Quick quality (SDXL-Lightning)
  - print sliders reuse the voxel work
  - the same picture reuses its 3D shape
  - ChatGPT images makes all options in one request; fal runs them in parallel
  - a built-in **Speed test**
  - Details in HANDOFF.md, under "Speed machinery".
- **Blender clay finish:**
  - A fourth 3D finish made by running the Blender.app on your Mac in the background: voxel remesh, smoothing, a fine
    clay surface texture, remesh and decimate.
  - Sliders: Clay texture, Texture size, Smoothness.
  - **No Blender app needed:** Blender's own Python package (`bpy`, about 230 MB) is installed inside Form Studio,
    in its own small environment (`~/FormStudio/vendor/blender-py`), by `setup-mac.sh` (skip with `--no-blender`)
    or the **Install Blender** button in Settings → Printing.
  - The Blender app works too, if you have it.
  - Settings → Printing shows whether Blender is ready, with an optional app path and **Look again**.
  - Without Blender (or if it fails), Soft is used, with a warning.
- **Colours per object (Print step):** pick each filament's colour (palette or custom), suggest from the picture,
  swap, or go back to the style's. The 3D view, legend and 3MF use them.
- **Look around on X, Y, Z:** axis gizmo, Front/Back/Left/Right/Top/Bottom/3/4 buttons, Spin, keys, views from
  underneath.
- **3 views (Shape step):** **Make 3 views** draws Front · Side · Back of the chosen look, without labels.
  - Drawn by ChatGPT images, Nano Banana, or free on this Mac (MV-Adapter, `setup-mac.sh --with-views`).
  - Meshy (multi-image), TRELLIS (multi) and Hunyuan3D-2 (fal multi-view, or local Hunyuan3D-2mv with
    `--with-hunyuan`) build the shape from all three; the other engines use the front.
  - Picking another option clears the views.
- **Clean up (Settings → Storage):**
  - Ticks off what can go: unfinished working files and downloads, the download cache, saved 3D shapes, older
    speed tests, earlier-version files, deleted styles no object uses, the Python download cache and old timing
    records.
  - "Models your engines don't use" is listed but not ticked.
  - Objects and styles are never touched.
- **Styles:** any style can be deleted.
  - Built-ins are hidden and yours move to `styles/.deleted`. Objects made with them keep working and show
    "(deleted)".
  - "Restore" sits at the bottom of the Styles page.
  - **New style** no longer has "Start from": it's built only from your name, words, pictures, colours and finish.
- **Error cards:**
  - A failed step shows a card inside the step: what happened in plain words, numbered steps to fix it, buttons to
    the right page (e.g. OpenAI Billing / Limits), **Use another engine**, **Try again**, and the service's own
    message folded away.
  - Long error toasts are readable cards that stay until closed.
- **OpenAI "Limit 0":**
  - It's recognised as the free tier (no images allowed) and explained with the steps to reach Usage tier 1.
  - It's no longer retried.
  - GPT Image 2.6 models are in the model list.
- **Style pictures pass on the look only:**
  - Local SDXL takes colour, material and surface from the first reference picture without pasting its objects in.
  - The style editor has "Only the look / Look and layout".
- **OpenAI errors** now quote OpenAI's own reason (no credit, rate limit or verification), and short rate limits are
  waited out. **Test key** also checks that the key can use the chosen image model.

- **Styles page cleanup**, the **New style** page with pictures at creation, **Start from**, colour suggestions,
  and the cleaned-up **editor** (save bar, unsaved-changes warning, multi-picture upload / paste, make first).
- **Style step:** options accumulate across runs and engines, appear one by one, and can be removed, cleared,
  viewed large and re-picked later.

---

## 10. Improvement ideas for "perfection" (prioritised)

### Must (experience breakers)
1. **First run made obvious:**
   - a 3-step welcome: choose free local or cloud → add keys or download models (with sizes and time) → try the
     sample object
   - no dead ends: engines that aren't ready say exactly what to do with one button
2. **One clear primary action per step,** with the next step highlighted. Replace "Use option N" / "Build 3D shape"
   / "Export print files" with one consistent **Continue →** pattern.
3. **Real progress everywhere:** loading into memory, each restyle step, 3D build, printify, with time estimates
   based on your Mac's past runs.
4. **Verify the 3MF in Bambu Studio and OrcaSlicer** and fix anything that doesn't open as two parts on two filaments.
5. **Clean failure recovery:** every error card gets **Try again**, **Use a cloud engine instead** and **Show
   details**.

### Should (quality and consistency)
6. **Built-in styles get curated reference pictures** (pixel, soft clay, low-poly) so every engine matches them
   better.
7. **Style preview / test bench:** run a style on 3 sample objects (mug, tree, shoe) to see it before using it.
8. **Compare view:** two to four options side by side, full size, with keyboard arrows and a "pick" shortcut.
9. **Print step presets:** "Small keychain 35 mm", "Fridge magnet 40 mm", "Desk figure 60 mm". Show the bed, the
   real size and an estimate of filament and print time.
10. **Undo/redo for print settings,** and a history of exports per object.
11. **Better shapes for thin, layered objects** (like the tree): a "keep detail" toggle that lowers the minimum
    feature and switches the 3D engine to a higher-detail model (Meshy / Hunyuan).
12. **Multi-view input:** add a side or back photo. Meshy's multi-image endpoint supports 1–4 views.

### Could (delight)
13. **A series view:** all objects of one style on one shelf (a 3D lineup) to check consistency.
14. **Batch:** drop 10 photos → restyle all → pick per object → export a plate with several objects arranged on one
    build plate.
15. **More colours:** per-region colours (accent filament) using 3MF painting.
16. **A share card:** export a render plus a printable card for the portfolio site (alwi.in case study).
17. **Packaged app:** a signed `.dmg` with auto-update and no Terminal.
18. **2D → 3D track:** SVG/PNG logos into reliefs, extrusions and cookie cutters, using the same finishes and formats.

### Polish checklist
- [ ] Consistent wording: "option", "style", "shape", "print" everywhere; no internal terms (seed, engine id) unless
  you ask for them
- [ ] Empty states with an example and a one-click sample for Library, Styles and the Style step
- [ ] Keyboard: ⌘N new object, ⌘S save style, ⌘E export, ←/→ options, Esc closes previews
- [ ] Every image zoomable; every long task cancellable
- [ ] Dark mode (today: light only)
- [ ] Accessibility: focus rings, labels, contrast checked, reduced motion
- [ ] Performance: viewer stays smooth with 100k-face voxel meshes (simplify for display only)
- [ ] Toasts don't cover buttons; errors stay until dismissed

---

## 11. Files to know

| Area | Files |
|---|---|
| Mac shell | `mac/main.swift`, `scripts/build-app.sh`, `scripts/setup-mac.sh` |
| API & worker | `backend/formstudio/api/app.py`, `api/worker.py`, `api/settings.py` |
| Pipeline | `backend/formstudio/pipeline.py`, `core/job.py`, `core/ingest.py` |
| Geometry | `core/printify.py` (finishes, formats, base, magnet, ring), `core/export.py` (3MF/STL/STEP), `core/primitives.py` |
| Styles | `core/style.py`, `core/colours.py`, `styles/*/style.yaml` |
| Engines | `engines/local.py`, `engines/cloud.py` (fal), `engines/openai_image.py`, `engines/meshy.py`, `engines/claude_sculpt.py`, `engines/offline.py` |
| Models | `backend/formstudio/models.py` |
| UI | `web/src/Library.tsx`, `JobPage.tsx`, `StylesPage.tsx`, `SettingsPage.tsx`, `Viewer.tsx`, `styles.css` |
| Tests | `backend/tests/*` (`FORMSTUDIO_SMOKE=1` for the real-model test) |
