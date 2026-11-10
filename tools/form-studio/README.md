# Form Studio

Photo → house style → printable 3D. A local command line tool for Apple Silicon Macs.

```
photo ─► cutout ─► styled candidates ─► (you pick one) ─► 3D mesh ─► printify ─► model.3mf
```

Every run writes a job folder, so you can stop, inspect, and resume at any stage.
See [PLAN.md](PLAN.md) for the design.

## Setup (macOS, Apple Silicon)

```sh
brew install python@3.11
cd tools/form-studio
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"             # core + tests (no models yet)
pip install -e ".[ai]"              # torch, diffusers, transformers (for real models)
pip install "git+https://github.com/VAST-AI-Research/TripoSR.git"   # reconstructor
# optional, for Hunyuan3D-2mini:  pip install -e ".[hunyuan]"  (see note below)
# optional, for STEP export:      pip install -e ".[step]"
```

Model weights download on first use (about 10 GB, cached in `~/.cache/huggingface`).
16 GB+ of unified memory is recommended. On 8 GB use `--fast` (SDXL-Turbo).

Run the tests:

```sh
pytest
```

## Walkthrough

```sh
formstudio new ~/photos/mug.jpg --style clay-minimal --name mug
formstudio stylize mug -n 4               # 02_candidates/1..4.png
formstudio confirm mug 2                  # 03_confirmed.png
formstudio build mug                      # 04_raw.glb → 05_print/body.stl, base.stl
formstudio export mug                     # 05_print/model.3mf (add --step for STEP)
```

Or everything in one go, stopping at the pick:

```sh
formstudio run ~/photos/mug.jpg --name mug             # stops after stylize
formstudio run ~/photos/mug.jpg --name mug --pick 2    # continues to 3MF
```

Without models, the whole flow runs with stub stages (passthrough styling and a plain
cylinder for the mesh). Use this to check the pipeline:

```sh
formstudio run sample.jpg --pick 1 --backend stub --matte threshold
```

### Commands

| Command | What it does |
| --- | --- |
| `new PHOTO --style S --name N` | Create a job, remove the background, and crop to a square (`--matte rembg\|threshold`). |
| `stylize N -n 4 [--fast]` | Generate candidates with SDXL + IP-Adapter + depth ControlNet (or SDXL-Turbo). |
| `confirm N I` | Keep candidate I. Resets later stages. |
| `build N --reconstructor triposr\|hunyuan\|stub` | Reconstruct the mesh, then printify it. Prints the report and warnings. |
| `export N [--step]` | Write `model.3mf` (and `model.step`) into `05_print/`. |
| `run PHOTO --pick I --backend stub\|triposr\|hunyuan` | Every stage in one command. |

Jobs go in `./jobs` by default. Set `FORMSTUDIO_JOBS` to change this.

## What the output means

- **body.stl / base.stl**: millimetres, z-up, the base sits on z=0. The overall height equals `height_mm` in the style.
- **model.3mf**: one object made of two parts. The body is on filament slot 1 and the base on slot 2. The palette colours are stored as named base materials. Bambu Studio and OrcaSlicer read the slot assignments from `Metadata/model_settings.config`.
- **manifest.json**: style name, style hash, seeds, model IDs, and a report for each stage. Use it to reproduce a result.

Printify fails with a clear message if the body cannot be made watertight, or if more than 1 % of the solid is thinner than `min_wall_mm`. It warns about steep overhangs.

## Writing a new style

Copy `styles/clay-minimal/` to a new folder and change `style.yaml`:

- `prompt` and `negative_prompt`: the look.
- `reference_image`: a picture of the target look. Its bytes are part of the style hash.
- `palette`: the body and base colours (hex).
- `height_mm`, `base_height_mm`, `base_margin_mm`, `cut_fraction`, `min_feature_mm`, `smooth_iters`, `target_faces`, `min_wall_mm`, `magnet`: the print geometry.

Changing any value changes the style hash, which is written to every job.

## Status and known limits

- Verified on the development machine with the stub backend: all tests pass (24), the full CLI flow runs end to end, and the outputs load back (3MF reads as two parts, STL is watertight).
- **Not yet verified:** the real SDXL / IP-Adapter / ControlNet stylize path, the TripoSR and Hunyuan3D reconstruct paths, and the STEP export. These need the model weights and a GPU, and were written against the library APIs without running them. Expect small fixes on first real run.
- **Not yet verified:** opening `model.3mf` in Bambu Studio or OrcaSlicer. Check that it shows two parts on two filaments without repair warnings before printing.
- STEP from an organic mesh is a faceted set of triangles, not CAD surfaces. STL and 3MF are the main formats.
