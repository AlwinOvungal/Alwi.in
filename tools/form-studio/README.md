# Form Studio

Turn a photo of any object into a miniature in your house style, then into a
two-colour 3D print file. It's a Mac app that runs on your own computer.

```
photo ──► cut-out ──► restyled options ──► you pick one ──► 3D shape ──► print-ready parts ──► 3MF / STL / STEP
```

The house style lives in [`styles/clay-minimal/`](styles/clay-minimal/):
matte, chunky, softened forms in muted olive on a bone-white base, based on
[`reference.png`](styles/clay-minimal/reference.png).

## Install (once)

You need macOS 12+ and [Homebrew](https://brew.sh). Everything else is free, and the script installs it.
Run each command on its own, in Terminal.

**1. Get the code.** Which command you use depends on whether you already have an `Alwi.in` folder in your home folder.

If you don't:

```bash
cd ~ && git clone https://github.com/AlwinOvungal/Alwi.in.git
```

If you do (`git clone` says *destination path 'Alwi.in' already exists*), update it instead:

```bash
cd ~/Alwi.in && git fetch origin
```

**2. Switch to the Form Studio branch:**

```bash
cd ~/Alwi.in && git checkout claude/inspiring-volta-4ljhbb && git pull
```

If `git checkout` says your local changes would be overwritten, stop there. Run `git status` to see
which files changed, and don't discard anything you want to keep.

**3. Run the setup:**

```bash
cd ~/Alwi.in/tools/form-studio && ./scripts/setup-mac.sh
```

The script sets up Python 3.11, Node, the local AI engines and the interface, and
then builds **Form Studio.app** in `~/Applications`. Open it from Launchpad or
Spotlight and drag it to your Dock.

| Option | What it does |
|---|---|
| `--cloud-only` | Skips the large local AI packages. Use this if you'll only run cloud or offline engines. |
| `--with-hunyuan` | Adds Hunyuan3D-2 mini, which gives better local 3D shapes. |
| `--with-step` | Adds STEP export. |

The first local run downloads the AI models (about 10 GB, once) into `~/.cache/huggingface`.
Your objects are saved in `~/FormStudio/jobs/`. Each one is a plain folder you can open in Finder.

**To update later:** run `git pull`, then `./scripts/setup-mac.sh` again.

## Using it

1. **Photo.** Drop in a photo and say what it is (e.g. "coffee mug"). The background is removed automatically.
2. **Style.** Generate options in the house style, then click the one you like and **Use option N**.
   The same photo with the same seeds always gives the same options. **New options** tries new seeds.
   **Skip: use photo as is** sends the cut-out straight to 3D, which is good for logos and 2D art.
3. **Shape.** Build the 3D shape from the chosen image.
4. **Print.** Choose what it becomes:
   - **Stand:** upright on a round base in the second colour.
   - **Magnet:** a flat back with a magnet pocket (10 × 2 mm by default).
   - **Keychain:** a flat back with a key-ring loop on top.

   Magnets and keychains print lying on their flat back, so they need no supports. Then look at it in 3D and tune the height, softness, thinnest part, base and magnet pocket.
   Each change rebuilds in a few seconds, with no AI involved. Then click **Export print files**.

For a **stand**, `model.3mf` opens in Bambu Studio and OrcaSlicer as **one object with two parts**: the body on
filament 1 and the base on filament 2. Load the matching colours in your AMS and slice.
`body.stl` and `base.stl` are there too if you'd rather print the parts separately and glue them.

## Engines

Pick default engines in **Settings**, and switch them for each object as you go.

| Step | Engine | Cost | Notes |
|---|---|---|---|
| Restyle | SDXL + style reference (local) | free | Steered by the style's first reference picture and the photo's depth. About 1–2 min per image on Apple Silicon. 16 GB+ of memory recommended; turn on *Fast mode* on 8 GB Macs. |
| Restyle | **ChatGPT images** (OpenAI) | a few cents to ~20¢ per image (by quality) | Redraws your object completely in the style, using the photo plus all the style's reference pictures in one call. Needs an OpenAI API key. |
| Restyle | Nano Banana (fal.ai) | about $0.04 per image | Sees your photo **and all the style's reference pictures**, so the look stays consistent. Needs a fal.ai key. |
| Restyle | FLUX Kontext (fal.ai) | about $0.025 per image | Strong at keeping the shape; uses only the style's words. Needs a fal.ai key. |
| Restyle | Palette preview (offline) | free | Instant recolour. No AI. |
| 3D | TripoSR (local) | free | MIT licence. Under a minute. |
| 3D | Hunyuan3D-2 mini (local) | free | Better shapes and slower. Install with `--with-hunyuan`. |
| 3D | **Meshy** (meshy.ai) | ~5–20 credits per object (≈ $0.10–0.40 on the Pro plan) | Strong, print-ready shapes in 1–3 minutes. API access needs a Meshy **Pro plan** (about $20/month); failed jobs are refunded. |
| 3D | TRELLIS (fal.ai) | about $0.02 per shape | The cheapest cloud 3D, under a minute. Needs a fal.ai key. |
| 3D | Hunyuan3D-2 mini (fal.ai) | about $0.10 per shape | Detailed shapes in about 30 s. Needs a fal.ai key. |
| 3D | **Claude Sculpt** (Anthropic) | about $0.02–0.06 per object | Claude rebuilds the object from soft building blocks. Very consistent and toy-like, and the recipe is saved so a rebuild is identical and free. Needs an Anthropic key. |
| 3D | Lathe / Extrude (offline) | free | Instant. Builds the shape from the silhouette: Lathe suits cups and vases, Extrude suits flat art. |

**Keys** go in **Settings** and are stored in your macOS Keychain, never in a file:
- **OpenAI key** (from [platform.openai.com](https://platform.openai.com) → API keys; billing must be set up) for ChatGPT images.
- **Meshy key** (from [meshy.ai](https://www.meshy.ai) → Settings → API; Pro plan or higher) for Meshy 3D.
- **fal.ai key** (from [fal.ai](https://fal.ai) → Dashboard → Keys) for Nano Banana, FLUX Kontext, TRELLIS and cloud Hunyuan3D.
- **Anthropic key** (from [console.anthropic.com](https://console.anthropic.com) → API Keys) for Claude Sculpt,
  automatic object naming and "Draft with Claude" on the Styles page.

### Claude Sculpt

Claude can't draw images or meshes, but it can describe a shape precisely. It looks at the chosen
image (and your style's references) and writes a **recipe**: a short list of rounded boxes,
capsules, spheres, cylinders, cones and rings, each placed and sized. Form Studio builds the 3D
model from that recipe ([`primitives.py`](backend/formstudio/core/primitives.py)) and saves it as
`04_recipe.json`. **Rebuild from Claude's recipe** is free and gives exactly the same shape;
**Ask Claude again** writes a new recipe.

## Styles: the image *and* the 3D shape

A style isn't a cut-out or a filter. The restyle engines redraw the object completely in the style, and
the style's **3D finish** then makes the printed *shape* follow it too, whichever engine built the 3D model:

| Built-in style | Restyle | 3D finish |
|---|---|---|
| Clay Minimal | matte olive clay, chunky, soft edges | **Soft**: blurred and smoothed, clay-like |
| Soft Clay | puffy pastel clay toy, squashed cute proportions | **Soft**, extra puffy, small details dropped |
| Pixel Voxel | 3D pixel art built from cubes, flat colours | **Pixel / voxel**: rebuilt from cubes (block size adjustable), with tiny bevels so it prints cleanly |
| Low Poly | flat-shaded geometric facets, papercraft | **Low-poly**: reduced to a few hundred flat faces |

You can change the finish for one object in the Print step, or for the whole style on the Styles page.

## How the style stays consistent

Two locks keep every object looking like part of the same family:

1. **Image lock.** The prompt, reference image, palette, seeds and model settings all come from
   `style.yaml`. Each object's `manifest.json` records the style's content hash, the seeds and the
   engines used, so you can reproduce any result.
2. **Geometry lock.** Whatever shape the AI returns, the same deterministic steps finish it
   ([`printify.py`](backend/formstudio/core/printify.py)):
   - voxelize and fill it, which makes it solid and closes any holes
   - remove parts thinner than the minimum feature size
   - blur and re-surface the volume for soft clay edges (always watertight)
   - smooth it
   - scale it to the set height
   - cut a flat bottom
   - add the round base, with a pocket so the body keys in
   - optionally cut a magnet pocket

### Making your own style

Open **Styles** in the app:
1. Type a name and click **Create style**, or **Duplicate** a built-in style.
2. Add 1–4 **reference pictures** that show the look you want.
3. Click **Draft with Claude**. It writes the restyle instruction, image prompt, sculpt guide,
   print colours and softness from your pictures. Review and edit them.
4. **Save style.** Each save bumps the style's version, and every object records the version it used.

Choose the style when you add a photo, or switch an object's style in its Photo step.
Your styles live in `~/FormStudio/styles/`; the built-in ones in this repo are read-only.

## Troubleshooting

**The app won't open, or closes by itself.** Form Studio writes everything to a log. Show the last lines:

```bash
tail -50 ~/FormStudio/formstudio.log
```

If the server fails to start, the app shows an error with these lines and an **Open Log** button.
Send the log to whoever helps you.

**The log says `cannot import name '__version__' from 'formstudio'`** (or "An old copy of Form Studio is in the way").
Files from an older version are being loaded instead of the current code. To fix it, run `./scripts/setup-mac.sh` again.
It moves the leftover folders to `~/FormStudio/old-version-leftovers/` (nothing is deleted) and removes the old install.

**Rebuild just the app** (after `git pull`, or after moving the folder):

```bash
cd ~/Alwi.in/tools/form-studio && ./scripts/build-app.sh
```

The build needs Apple's free Command Line Tools. If `swiftc` is missing, install them with `xcode-select --install`.

**Where everything is.** **Settings → Storage** lists every folder Form Studio uses, with its size and a
**Show in Finder** button:
- the app: `~/Applications/Form Studio.app`
- its code: `~/Alwi.in/tools/form-studio`, with the Python environment in `.venv`
- your objects and styles: `~/FormStudio`
- the local AI models: `~/.cache/huggingface` and `~/.u2net`

Cloud engines download nothing. If you only use cloud engines, you can delete the models folder; the models
download again if a local engine needs them.

**Use it in your browser instead of the app window:**

```bash
cd ~/Alwi.in/tools/form-studio && .venv/bin/formstudio-cli serve
```

## Running it without the app window

```bash
.venv/bin/formstudio-cli serve
```

This opens the app in your browser at http://127.0.0.1:8765.

```bash
.venv/bin/formstudio-cli run ~/Desktop/mug.jpg --subject "coffee mug" --pick 1
```

This runs the whole pipeline in one go and is handy for batches.

## Development

```bash
.venv/bin/pip install -e "backend[dev]" && (cd backend && ../.venv/bin/pytest -q)
```

Runs the backend tests (offline engines only, no GPU needed).

```bash
(cd web && npm run dev)
```

This starts the UI with hot reload at http://localhost:5173. It needs `formstudio-cli serve` running on port 8765.

- `backend/formstudio/core/`: style, job folders, background removal, printify, export
- `backend/formstudio/engines/`: local (SDXL, TripoSR, Hunyuan3D), cloud (fal.ai) and offline engines
- `backend/formstudio/api/`: FastAPI server, task worker with live progress over Server-Sent Events, settings and Keychain
- `mac/main.swift`: the native Mac app (window, menus, downloads), which runs the Python server in the background
- `backend/formstudio/desktop.py`: server entry point (`--serve` for the app, `--browser` for a browser)
- `web/`: React + three.js interface, built into `backend/formstudio/web_dist/`
