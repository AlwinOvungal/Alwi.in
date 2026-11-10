# Form Studio — photo → house style → printable 3D (local CLI)

## Context
You want a tool that turns everyday objects (e.g. a photo of a coffee mug) into one consistent, repeatable aesthetic, and then into a file you can send to a 3D printer. The long-term goal is 2D PNG/vector → 3D print. This first stage is **real photo → styled image (you confirm it) → 3D mesh → print-ready export**.

The reference style (your screenshot) is: matte, clay-like, chunky simplified forms, soft rounded edges, a muted olive green with white, no fine detail, toy-like scale. Your choices: **local CLI first**, **local open-source models on GPU**, **style only (no pot; the object stands on its own flat base)**, **FDM multi-colour (AMS/MMU) → 3MF**.

The alwi.in repo is a static portfolio site with no backend, so the tool goes in a self-contained folder, `tools/form-studio/`, that the site doesn't touch. It can move into its own repo later without changes.

**Runtime target: your Apple Silicon Mac, free, no subscriptions.** All models are open source and run locally on PyTorch's MPS backend (Apple's GPU). You're in India, so Hunyuan3D's licence allows you to use it. 16 GB+ of unified memory is recommended; on 8 GB, use SDXL-Turbo.

**What happens in this cloud session now:** this plan is saved to the repo as `tools/form-studio/PLAN.md`, with your screenshot as `tools/form-studio/styles/clay-minimal/reference.png`, committed and pushed to `claude/inspiring-volta-4ljhbb`. Building the tool continues in **Claude Code on your Mac**: pull the branch, run `claude` in the repo, and say "implement tools/form-studio/PLAN.md".

## Key idea: consistency comes from two locks
1. **Image lock:** a versioned *style spec* (prompt template, negative prompt, reference image(s), palette, seed, model IDs). The same spec plus the same photo gives the same candidates.
2. **Geometry lock:** after AI reconstruction, deterministic post-processing *forces* the style whatever the AI produced: smoothing and remeshing for the soft clay look, removal of small details, colours snapped to the palette, a standard base, and a standard scale. This is what keeps a series looking like one family.

## Pipeline (each stage writes into a job folder so you can stop, inspect and resume)
```
jobs/<name>/
  00_input.jpg
  01_cutout.png          background removed, centred, square
  02_candidates/*.png    N styled options (fixed seeds)
  03_confirmed.png       the one you picked
  04_raw.glb             AI mesh
  05_print/
     body.stl  base.stl  model.3mf  (model.step optional)
  manifest.json          style hash, seeds, model versions, params → full reproducibility
```

1. **Ingest:** remove the background with BiRefNet (via `rembg`), crop to the object, pad to a square on white.
2. **Stylize:** SDXL + IP-Adapter (style from your reference screenshot) + ControlNet-depth (keeps the mug's real silhouette) → N candidates on a plain white background. Runs on `mps` in fp16 with attention slicing; `--fast` switches to SDXL-Turbo for low-memory Macs. Device auto-detect order: `mps` → `cuda` → `cpu`.
3. **Confirm:** `formstudio confirm jobs/mug 3` copies candidate 3 to `03_confirmed.png`. This is your approval step.
4. **Reconstruct:** confirmed image → mesh. **Default on Mac: TripoSR** (MIT licence, runs on MPS/CPU, under a minute). Optional backend: **Hunyuan3D-2mini** (shape only), for better forms, tried on MPS with a CPU fallback. Both sit behind a `Reconstructor` interface. Colour is not taken from the AI texture; it comes from the palette in step 5.
5. **Printify** (CPU; trimesh + manifold3d + pymeshlab):
   - convert the axes from y-up to z-up, keep the largest component, repair until watertight (manifold)
   - clay look: voxel remesh at the spec's `min_feature_mm`, Taubin smoothing, decimation to the target face count
   - scale to the spec's `height_mm` (default 50), cut a flat bottom at a set fraction of the height
   - **base:** a parametric rounded plinth (radius from the footprint plus a margin), unioned under the body as a separate part
   - check printability: wall thickness ≥ 1.2 mm (voxel erosion test), overhang report, fail or warn with clear messages
   - optional `--magnet 6x2` pocket cut into the base underside (like the reference pieces)
6. **Export:**
   - `body.stl` and `base.stl` (millimetres)
   - `model.3mf`: one object with two parts, with filament slots assigned through `Metadata/model_settings.config` (Bambu Studio / OrcaSlicer) and colour names in the base materials, so it opens ready to slice in two colours
   - `--step`: a faceted STEP via OCP (an optional extra). Note: a STEP made from an organic mesh is triangles, not true CAD surfaces. STL/3MF are the main formats.

## Files to create (`tools/form-studio/`)
- `pyproject.toml`: package `formstudio`, CLI entry `formstudio`. Core deps: `typer numpy pillow pyyaml trimesh manifold3d pymeshlab scipy rembg`. Extras: `[ai]` = `torch diffusers transformers accelerate` + TripoSR (from git), `[hunyuan]` = `hy3dgen` (from git), `[step]` = `cadquery-ocp`. Python 3.11.
- `README.md`: Mac setup (`brew install python@3.11`, venv, `pip install -e ".[ai]"`), first-run model downloads (about 10 GB, cached in `~/.cache/huggingface`), the walkthrough, how to write a new style.
- `styles/clay-minimal/style.yaml` + `reference.png` (your screenshot): prompt/negative prompt, seeds, IP-Adapter scale, ControlNet scale, palette `{body: "#5E6B2E" olive, base: "#F2F0EA" white}`, `height_mm`, `min_feature_mm`, `smooth_iters`, `target_faces`, base params.
- `formstudio/style.py`: `StyleSpec` dataclass, loader, and a content hash written to the manifest.
- `formstudio/job.py`: job folder layout, `manifest.json` read/write, stage skip/resume.
- `formstudio/ingest.py`, `stylize.py`, `reconstruct.py` (`Reconstructor` protocol + `Hunyuan3DMini`, `TripoSR`, and a `Stub` backend that returns a primitive mesh for testing without a GPU), `printify.py`, `export.py` (STL, hand-written 3MF zip with the slicer config, optional STEP).
- `formstudio/cli.py`: commands `new <photo> --style clay-minimal`, `stylize`, `confirm`, `build`, `export`, `run` (all stages; stops at confirm unless `--pick N` is given).
- `tests/`: pytest for printify and export using synthetic meshes.
- `.gitignore` additions: `tools/form-studio/jobs/`, model caches.

## Example usage
```
formstudio new ~/photos/mug.jpg --style clay-minimal --name mug
formstudio stylize mug -n 4
formstudio confirm mug 2
formstudio build mug          # reconstruct + printify
formstudio export mug         # → jobs/mug/05_print/model.3mf
```

## Roadmap (after this stage, not in scope now)
- **2D PNG/SVG → 3D:** vectorise with vtracer/potrace → extrude, emboss or make a relief on the same style base. It reuses `printify`/`export` unchanged.
- Accent colours (per-triangle `paint_color` in 3MF) for objects that need more than 2 colours.
- More style presets; a small web UI; a case-study page on alwi.in.

## Verification
- **Now (this session):** confirm that `tools/form-studio/PLAN.md` and `reference.png` are pushed to the branch.
- **When implementing on your Mac:** `pytest` for printify and export. Assert the mesh is watertight, the base sits flat at z=0, the height matches the spec, wall thickness passes, and the 3MF unzips with 2 parts and 2 filament assignments. Then run `formstudio run sample.jpg --pick 1` end to end with `--backend stub` (stylize passes the image through; reconstruct returns a stub mesh), so the whole CLI flow and the exports are exercised.
- **Then with real models:** install `.[ai]`, run the mug example, check that the candidates match the reference style, open `model.3mf` in Bambu Studio or OrcaSlicer (it should show two parts on two filaments, with no repair warnings), and print a test piece.
- Commit to `claude/inspiring-volta-4ljhbb` and push.
