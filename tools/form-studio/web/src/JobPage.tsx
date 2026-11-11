import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { api, Engines, fileUrl, Job, PrintFormat, PrintParams, reached, Settings, Style, useJob } from "./api";
import { EnginePicker, go, Progress, Slider, Switch, useToast } from "./ui";

const Viewer = lazy(() => import("./Viewer"));

type StepId = "photo" | "style" | "shape" | "print";
const STEPS: { id: StepId; title: string; hint: string }[] = [
  { id: "photo", title: "Photo", hint: "Background removed" },
  { id: "style", title: "Style", hint: "Pick a look" },
  { id: "shape", title: "Shape", hint: "Image → 3D" },
  { id: "print", title: "Print", hint: "Tune & export" },
];

function stepFor(job: Job): StepId {
  if (reached(job, "reconstructed")) return "print";
  if (reached(job, "confirmed")) return "shape";
  if (reached(job, "stylized")) return "style";
  return "photo";
}

function stepDone(job: Job, s: StepId) {
  return s === "photo" ? reached(job, "cutout") : s === "style" ? reached(job, "confirmed") : s === "shape" ? reached(job, "reconstructed") : job.stage === "exported";
}

function stepEnabled(job: Job, s: StepId) {
  return s === "photo" || (s === "style" && reached(job, "cutout")) || (s === "shape" && reached(job, "confirmed")) || (s === "print" && reached(job, "printified"));
}

export default function JobPage({ id }: { id: string }) {
  const { job, setJob, error } = useJob(id);
  const [step, setStep] = useState<StepId | null>(null);
  const [engines, setEngines] = useState<Engines | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [style, setStyle] = useState<Style | null>(null);
  const toast = useToast();
  const lastStage = useRef<string | null>(null);

  useEffect(() => {
    api.engines().then(setEngines).catch(() => {});
    api.settings().then(setSettings).catch(() => {});
  }, []);
  useEffect(() => {
    if (!job) return;
    if (!style) api.styles().then((all) => setStyle(all.find((s) => s.id === job.style.id) ?? all[0]));
    // move forward automatically when a step finishes
    if (lastStage.current !== job.stage) {
      if (lastStage.current === null || step === null || stepFor(job) !== step) setStep(stepFor(job));
      lastStage.current = job.stage;
    }
  }, [job]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (job?.task?.status === "error" && job.task.error) toast(job.task.error, true);
  }, [job?.task?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <div className="page"><p className="note bad">{error}</p><a className="btn" href="#/">Back to library</a></div>;
  if (!job || !step) return <div className="page muted">Loading…</div>;

  const busy = !!job.task && (job.task.status === "queued" || job.task.status === "running");
  const run = async (p: Promise<Job>) => {
    try {
      setJob(await p);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  return (
    <div className="page">
      <div className="job-head">
        <div className="stack" style={{ gap: 4 }}>
          <a href="#/" className="label" style={{ textDecoration: "none" }}>← Library</a>
          <input
            className="name"
            defaultValue={job.name}
            size={Math.max(6, job.name.length)}
            aria-label="Object name"
            onBlur={(e) => e.target.value.trim() && e.target.value !== job.name && run(api.rename(job.id, { name: e.target.value.trim() }))}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          />
        </div>
        <div className="row">
          {style && (
            <span className="chip" title={`Style hash ${job.style.hash}`}>
              <span className="swatches">
                {Object.values(style.palette).map((c) => <span key={c.hex} className="swatch" style={{ background: c.hex, width: 10, height: 10 }} />)}
              </span>
              {style.name} v{style.version}
            </span>
          )}
          <button
            className="btn sm ghost danger"
            disabled={busy}
            onClick={async () => {
              if (!confirm(`Delete “${job.name}” and all its files?`)) return;
              try {
                await api.remove(job.id);
                go("/");
              } catch (e) {
                toast((e as Error).message, true);
              }
            }}
          >
            Delete
          </button>
        </div>
      </div>

      <div className="job">
        <ol className="steps">
          {STEPS.map((s, i) => (
            <li key={s.id} className={stepDone(job, s.id) ? "done" : ""}>
              <button className={step === s.id ? "active" : ""} disabled={!stepEnabled(job, s.id)} onClick={() => setStep(s.id)}>
                <span className="num">{stepDone(job, s.id) ? "✓" : i + 1}</span>
                <span className="t">
                  {s.title}
                  <small>{s.hint}</small>
                </span>
              </button>
            </li>
          ))}
        </ol>

        <section className="step-body" aria-live="polite">
          {step === "photo" && <PhotoStep job={job} busy={busy} engines={engines} settings={settings} run={run} />}
          {step === "style" && <StyleStep job={job} busy={busy} style={style} settings={settings} run={run} />}
          {step === "shape" && <ShapeStep job={job} busy={busy} engines={engines} settings={settings} run={run} />}
          {step === "print" && <PrintStep job={job} busy={busy} style={style} settings={settings} run={run} />}
        </section>
      </div>
    </div>
  );
}

/** The preferred engine if it's usable, otherwise the first one that is. */
function pickAvailable(list: Engines["stylize"] | undefined, preferred: string | undefined): string {
  if (!list) return "";
  return list.find((e) => e.id === preferred && e.available)?.id ?? list.find((e) => e.available)?.id ?? "";
}

type StepProps = {
  job: Job;
  busy: boolean;
  settings: Settings | null;
  run: (p: Promise<Job>) => Promise<void>;
};

// ---------------------------------------------------------------- 1. photo ----
function PhotoStep({ job, busy, engines, settings, run }: StepProps & { engines: Engines | null }) {
  const [engine, setEngine] = useState<string>("");
  const [subject, setSubject] = useState(job.subject);
  useEffect(() => setEngine((e) => e || pickAvailable(engines?.stylize, settings?.engines.stylize)), [settings, engines]);
  const working = busy && job.task?.kind === "stylize";

  return (
    <>
      <div className="pair">
        <figure className="frame" style={{ margin: 0 }}>
          {job.input && <img src={fileUrl(job, job.input)} alt="Original photo" />}
          <figcaption className="chip">Original</figcaption>
        </figure>
        <figure className="frame" style={{ margin: 0 }}>
          {job.files.includes("01_cutout.png") ? (
            <img src={fileUrl(job, "01_cutout.png")} alt="Background removed" />
          ) : (
            <div className="muted small" style={{ aspectRatio: "1", display: "grid", placeItems: "center", padding: 24, textAlign: "center" }}>
              The cut-out will be prepared again from your photo when you generate options.
            </div>
          )}
          <figcaption className="chip">Cut out</figcaption>
        </figure>
      </div>
      <div className="split">
        <div className="card panel stack">
          <h2>Restyle it</h2>
          <p className="muted small" style={{ margin: 0 }}>
            The style model redraws your object as a clay miniature, keeping its shape. You get several options and pick one.
          </p>
          <label className="field">
            <span>What is the object? (e.g. coffee mug)</span>
            <input className="input" value={subject} onChange={(e) => setSubject(e.target.value)} onBlur={() => subject !== job.subject && run(api.rename(job.id, { subject }))} />
          </label>
          <span className="small muted">Want a keychain, magnet or stand? Choose that in the Print step.</span>
          {engines && <EnginePicker engines={engines.stylize} value={engine} onChange={setEngine} />}
        </div>
        <div className="card panel stack">
          {working && job.task ? (
            <Progress task={job.task} />
          ) : (
            <>
              <button className="btn olive" disabled={busy || !engine} onClick={async () => {
                if (subject !== job.subject) await api.rename(job.id, { subject });
                run(api.stylize(job.id, engine));
              }}>
                Generate {settings?.candidates ?? 4} options
              </button>
              <button className="btn" disabled={busy} onClick={() => run(api.confirm(job.id, null))} title="Skip restyling: use the cut-out photo for the 3D step">
                Skip: use photo as is
              </button>
              <p className="small muted" style={{ margin: 0 }}>Skip restyling for objects already in the style, or for 2D art and logos.</p>
            </>
          )}
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- 2. style ----
function StyleStep({ job, busy, style, settings, run }: StepProps & { style: Style | null }) {
  const [sel, setSel] = useState<number | null>(job.confirmed?.index ?? null);
  const working = busy && job.task?.kind === "stylize";
  const n = settings?.candidates ?? 4;

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="stack" style={{ gap: 4 }}>
          <h2>Pick the one that feels right</h2>
          <span className="muted small">Same photo + same seeds = same options. Generate more for new seeds.</span>
        </div>
        {style?.has_reference && (
          <div className="ref" style={{ gridTemplateColumns: "48px auto" }}>
            <img src={`/api/styles/${style.id}/reference`} alt="Style reference" style={{ width: 48, height: 58 }} />
            <span className="small muted">Style<br />reference</span>
          </div>
        )}
      </div>

      {working && job.task && <div className="card panel"><Progress task={job.task} /></div>}

      <div className="cands">
        {working && job.candidates.length === 0
          ? Array.from({ length: n }, (_, i) => <div key={i} className="skeleton" />)
          : job.candidates.map((c, i) => (
              <button key={c.file} className={`card cand ${sel === i ? "sel" : ""}`} onClick={() => setSel(i)} disabled={working}>
                <img src={fileUrl(job, c.file)} alt={`Option ${i + 1}`} />
                <span className="cap small">
                  <span>Option {i + 1}</span>
                  <span className="label">seed {c.seed}</span>
                </span>
              </button>
            ))}
      </div>

      <div className="row">
        <button className="btn olive" disabled={busy || sel === null} onClick={() => sel !== null && run(api.confirm(job.id, sel))}>
          Use option {sel !== null ? sel + 1 : ""} →
        </button>
        <button className="btn" disabled={busy} onClick={() => run(api.stylize(job.id, job.candidates[0]?.engine, Math.floor(Math.random() * 1e6)))}>
          New options
        </button>
        <button className="btn ghost" disabled={busy} onClick={() => run(api.stylize(job.id, job.candidates[0]?.engine))}>
          Back to style seeds
        </button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- 3. shape ----
function ShapeStep({ job, busy, engines, settings, run }: StepProps & { engines: Engines | null }) {
  const [engine, setEngine] = useState("");
  useEffect(
    () => setEngine((e) => e || pickAvailable(engines?.reconstruct, job.reconstruct?.engine ?? settings?.engines.reconstruct)),
    [settings, engines, job.reconstruct],
  );
  const working = busy && job.task?.kind === "reconstruct";

  return (
    <div className="split">
      <figure className="frame" style={{ margin: 0 }}>
        <img src={fileUrl(job, "03_confirmed.png")} alt="Chosen style" />
        <figcaption className="chip">Chosen look</figcaption>
      </figure>
      <div className="stack">
        <div className="card panel stack">
          <h2>Build the 3D shape</h2>
          <p className="small muted" style={{ margin: 0 }}>The 3D model guesses the back and sides from this one image. Form Studio then cleans it up for printing.</p>
          {engines && <EnginePicker engines={engines.reconstruct} value={engine} onChange={setEngine} />}
        </div>
        <div className="card panel stack">
          {working && job.task ? (
            <Progress task={job.task} />
          ) : (
            <button className="btn olive" disabled={busy || !engine} onClick={() => run(api.reconstruct(job.id, engine))}>
              {reached(job, "reconstructed") ? "Rebuild shape" : "Build 3D shape"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 4. print ----
const FORMAT_HINT: Record<PrintFormat, string> = {
  stand: "Stands upright on a round base in the second colour.",
  magnet: "Flat back with a magnet pocket. Prints lying down, no supports.",
  keychain: "Flat back with a key-ring loop on top. Prints lying down, no supports.",
};

type Overrides = { [K in keyof PrintParams]?: PrintParams[K] extends object ? Partial<PrintParams[K]> : PrintParams[K] };

function PrintStep({ job, busy, style, settings, run }: StepProps & { style: Style | null }) {
  const [view, setView] = useState<"print" | "raw">("print");
  const [p, setP] = useState<PrintParams | null>(job.print_params);
  const [applying, setApplying] = useState(false);
  const [formats, setFormats] = useState<string[]>(settings?.export_formats ?? ["3mf"]);
  const toast = useToast();
  useEffect(() => setP(job.print_params), [job.print_params]);

  const apply = async (o: Overrides) => {
    setApplying(true);
    try {
      await run(api.printify(job.id, o));
    } finally {
      setApplying(false);
    }
  };
  // sliders preview the value instantly and rebuild when released
  type Section = Exclude<keyof PrintParams, "format">;
  const set = <K extends Section>(k: K, patch: Partial<PrintParams[K]>, commit = false) => {
    setP((cur) => (cur ? { ...cur, [k]: { ...cur[k], ...patch } } : cur));
    if (commit) apply({ [k]: patch } as Overrides);
  };
  const setFormat = (f: PrintFormat) => {
    setP((cur) => (cur ? { ...cur, format: f } : cur));
    apply({ format: f });
  };

  const rep = job.report;
  const body = style?.palette.body.hex ?? "#5E6B2E";
  const base = style?.palette.base.hex ?? "#F2F0EA";
  const hasBase = job.files.includes("05_print/base.stl");
  const exported = job.stage === "exported";
  const slicer = settings?.slicer ?? "bambu";
  const slicerName = { bambu: "Bambu Studio", orca: "OrcaSlicer", prusa: "PrusaSlicer" }[slicer];

  const rebuilding = applying || (busy && job.task?.kind === "reconstruct");
  const exportNow = async () => {
    try {
      const j = await api.export(job.id, formats);
      run(Promise.resolve(j));
      toast("Print files ready");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const fileList = useMemo(() => job.exports.map((f) => ({ f, ext: f.split(".").pop()!.toUpperCase(), name: f.split("/").pop()! })), [job.exports]);

  if (!p) return null;
  const fmt: PrintFormat = p.format ?? "stand";
  return (
    <div className="split">
      <div className="stack">
        <div className="viewer">
          <Suspense fallback={<div className="busy muted">Loading 3D…</div>}>
            <Viewer
              mode={view}
              bodyUrl={fileUrl(job, "05_print/body.stl")}
              baseUrl={hasBase ? fileUrl(job, "05_print/base.stl") : undefined}
              rawUrl={fileUrl(job, "04_raw.glb")}
              bodyColor={body}
              baseColor={base}
              flat={fmt !== "stand"}
            />
          </Suspense>
          <div className="overlay">
            <div className="seg" role="tablist">
              <button className={view === "print" ? "on" : ""} onClick={() => setView("print")}>Print</button>
              <button className={view === "raw" ? "on" : ""} onClick={() => setView("raw")}>AI mesh</button>
            </div>
          </div>
          {view === "print" && (
            <div className="legend">
              <span><i className="swatch" style={{ background: body }} />{style?.palette.body.name} · filament 1</span>
              {hasBase && <span><i className="swatch" style={{ background: base }} />{style?.palette.base.name} · filament 2</span>}
            </div>
          )}
          {rebuilding && <div className="busy"><span className="chip">Rebuilding…</span></div>}
        </div>

        {rep && (
          <div className="card panel stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3>Printability</h3>
              <span className={`chip ${rep.watertight ? "" : "offline"}`}><span className="dot" />{rep.watertight ? "Watertight" : "Needs repair"}</span>
            </div>
            <div className="stats">
              <div><span className="label">Size (mm)</span><b>{rep.size_mm.join(" × ")}</b></div>
              <div><span className="label">Material</span><b>{(rep.body_volume_cm3 + rep.base_volume_cm3).toFixed(1)} cm³</b></div>
              <div><span className="label">Thinnest part</span><b>≥ {rep.min_feature_mm} mm</b></div>
              <div><span className="label">Overhangs &gt; 45°</span><b>{rep.overhang_pct}%</b></div>
            </div>
            {rep.warnings.map((w) => <div key={w} className="note warn">{w}</div>)}
          </div>
        )}
      </div>

      <div className="stack">
        <div className="card panel stack">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3>Shape</h3>
            <button className="btn sm ghost" disabled={busy} onClick={() => run(api.resetPrint(job.id))}>Reset to style</button>
          </div>
          <div className="seg" role="radiogroup" aria-label="Format" style={{ alignSelf: "start" }}>
            {(style?.formats ?? [{ id: "stand", label: "Stand" }]).map((f) => (
              <button key={f.id} role="radio" aria-checked={fmt === f.id} className={fmt === f.id ? "on" : ""} disabled={busy} onClick={() => fmt !== f.id && setFormat(f.id)}>
                {f.label}
              </button>
            ))}
          </div>
          <p className="small muted" style={{ margin: 0 }}>{FORMAT_HINT[fmt]}</p>
          <Slider label={fmt === "stand" ? "Height" : "Object height"} unit=" mm" min={15} max={180} step={1} value={p.geometry.height_mm} onChange={(v) => set("geometry", { height_mm: v })} onCommit={(v) => set("geometry", { height_mm: v }, true)} disabled={busy} />
          <Slider label="Softness" unit=" mm" min={0} max={3} step={0.1} value={p.geometry.soften_mm} onChange={(v) => set("geometry", { soften_mm: v })} onCommit={(v) => set("geometry", { soften_mm: v }, true)} disabled={busy} />
          <Slider label="Thinnest part" unit=" mm" min={0.8} max={5} step={0.1} value={p.geometry.min_feature_mm} onChange={(v) => set("geometry", { min_feature_mm: v })} onCommit={(v) => set("geometry", { min_feature_mm: v }, true)} disabled={busy} />
          <Slider label="Detail (remesh)" unit=" mm" min={0.25} max={1.5} step={0.05} value={p.geometry.voxel_mm} onChange={(v) => set("geometry", { voxel_mm: v })} onCommit={(v) => set("geometry", { voxel_mm: v }, true)} disabled={busy} />
          <div className="divider" />
          {fmt !== "stand" && (
            <Slider label="Thickness" unit=" mm" min={3} max={25} step={0.5} value={p.flat_back.thickness_mm} onChange={(v) => set("flat_back", { thickness_mm: v })} onCommit={(v) => set("flat_back", { thickness_mm: v }, true)} disabled={busy} />
          )}
          {fmt === "keychain" && (
            <>
              <Slider label="Key-ring hole" unit=" mm" min={3} max={8} step={0.5} value={p.ring.hole_mm} onChange={(v) => set("ring", { hole_mm: v })} onCommit={(v) => set("ring", { hole_mm: v, outer_radius_mm: Math.max(p.ring.outer_radius_mm, v / 2 + 2) }, true)} disabled={busy} />
              <Slider label="Loop thickness" unit=" mm" min={2} max={6} step={0.5} value={p.ring.thickness_mm} onChange={(v) => set("ring", { thickness_mm: v })} onCommit={(v) => set("ring", { thickness_mm: v }, true)} disabled={busy} />
            </>
          )}
          {fmt === "stand" && (
            <>
              <Switch label="Base" checked={p.base.enabled} onChange={(v) => set("base", { enabled: v }, true)} disabled={busy} />
              {p.base.enabled && (
                <>
                  <Slider label="Base margin" unit=" mm" min={0} max={15} step={0.5} value={p.base.margin_mm} onChange={(v) => set("base", { margin_mm: v })} onCommit={(v) => set("base", { margin_mm: v }, true)} disabled={busy} />
                  <Slider label="Base thickness" unit=" mm" min={1.2} max={10} step={0.2} value={p.base.thickness_mm} onChange={(v) => set("base", { thickness_mm: v })} onCommit={(v) => set("base", { thickness_mm: v }, true)} disabled={busy} />
                </>
              )}
            </>
          )}
          {fmt !== "keychain" && (
            <Switch label="Magnet pocket" checked={p.magnet.enabled} onChange={(v) => set("magnet", { enabled: v }, true)} disabled={busy} />
          )}
          {fmt !== "keychain" && p.magnet.enabled && (
            <div className="pair" style={{ gap: 10 }}>
              <label className="field"><span>Ø mm</span><input className="input" type="number" step="0.5" min="2" max="30" value={p.magnet.diameter_mm} onChange={(e) => set("magnet", { diameter_mm: Number(e.target.value) })} onBlur={(e) => set("magnet", { diameter_mm: Number(e.target.value) }, true)} /></label>
              <label className="field"><span>Depth mm</span><input className="input" type="number" step="0.5" min="0.5" max="10" value={p.magnet.depth_mm} onChange={(e) => set("magnet", { depth_mm: Number(e.target.value) })} onBlur={(e) => set("magnet", { depth_mm: Number(e.target.value) }, true)} /></label>
            </div>
          )}
        </div>

        <div className="card panel stack">
          <h3>Export</h3>
          <div className="row small">
            {[["3mf", hasBase ? "3MF (two colours)" : "3MF"], ["step", "STEP"]].map(([f, label]) => (
              <label key={f} className="row" style={{ gap: 6 }}>
                <input type="checkbox" checked={formats.includes(f)} onChange={(e) => setFormats((cur) => (e.target.checked ? [...cur, f] : cur.filter((x) => x !== f)))} />
                {label}
              </label>
            ))}
          </div>
          <button className="btn olive" disabled={busy || applying} onClick={exportNow}>{exported ? "Export again" : "Export print files"}</button>
          {exported && (
            <>
              <div className="files">
                {fileList.map(({ f, ext, name }) => (
                  <div key={f} className="file">
                    <span className="ext">{ext}</span>
                    <span className="small">{name}</span>
                    <a className="btn sm" href={fileUrl(job, f, true)} download={`${job.id}-${name}`}>Save</a>
                  </div>
                ))}
              </div>
              <div className="row">
                {job.exports.some((f) => f.endsWith(".3mf")) && (
                  <button className="btn sm grow" onClick={() => api.open(job.id, slicer, "05_print/model.3mf").catch((e) => toast(e.message, true))}>Open in {slicerName}</button>
                )}
                <button className="btn sm ghost" onClick={() => api.open(job.id, "finder", "05_print/body.stl").catch((e) => toast(e.message, true))}>Show in Finder</button>
              </div>
              <p className="small muted" style={{ margin: 0 }}>
                {hasBase
                  ? "The 3MF opens as one object with two parts: body on filament 1, base on filament 2. Load the matching colours in your AMS."
                  : "The 3MF opens as one part on filament 1. It lies flat on its back, so it needs no supports."}
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
