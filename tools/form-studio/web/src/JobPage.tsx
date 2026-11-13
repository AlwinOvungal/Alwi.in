import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { api, BACKERS, Candidate, Colour, Engines, FILAMENTS, fileUrl, Finish, FINISHES, formatSeconds, Job, PrintFormat, PrintParams, Quality, QUALITIES, reached, Settings, Style, useJob } from "./api";
import { StylePicker } from "./Library";
import { ClayFields, EnginePicker, ErrorCard, go, NeedsBlender, Progress, Slider, Switch, useBlender, useToast } from "./ui";

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

/** How long a finished step took on this object ("took 48 s"). */
function stepTime(job: Job, s: StepId): string | null {
  const t = job.timings;
  if (!t) return null;
  const secs =
    s === "photo" ? t.cutout?.seconds : s === "style" ? t.stylize?.seconds : s === "shape" ? t.reconstruct?.seconds
      : t.printify || t.export ? (t.printify?.seconds ?? 0) + (t.export?.seconds ?? 0) : undefined;
  if (secs == null || !stepDone(job, s)) return null;
  return s === "shape" && t.reconstruct?.cached ? "reused" : formatSeconds(secs);
}

/** Best / Quick / Lightest, for the local SDXL engine. */
function QualityPicker({ value, onChange, disabled }: { value: Quality; onChange: (q: Quality) => void; disabled?: boolean }) {
  return (
    <div className="field">
      <span>Quality</span>
      <div className="seg wide" role="radiogroup" aria-label="Local restyle quality">
        {QUALITIES.map((q) => (
          <button key={q.id} type="button" role="radio" aria-checked={value === q.id} className={value === q.id ? "on" : ""} disabled={disabled} title={q.hint} onClick={() => onChange(q.id)}>
            {q.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function stepEnabled(job: Job, s: StepId) {
  return s === "photo" || (s === "style" && reached(job, "cutout")) || (s === "shape" && reached(job, "confirmed")) || (s === "print" && reached(job, "printified"));
}

export default function JobPage({ id }: { id: string }) {
  const { job, setJob, error } = useJob(id);
  const [step, setStep] = useState<StepId | null>(null);
  const [engines, setEngines] = useState<Engines | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [styles, setStyles] = useState<Style[]>([]);
  const toast = useToast();
  const lastStage = useRef<string | null>(null);

  useEffect(() => {
    api.engines().then(setEngines).catch(() => {});
    api.settings().then(setSettings).catch(() => {});
    api.styles().then(setStyles).catch(() => {});
  }, []);
  useEffect(() => {
    if (!job) return;
    // move forward automatically when a step finishes
    if (lastStage.current !== job.stage) {
      if (lastStage.current === null || step === null || stepFor(job) !== step) setStep(stepFor(job));
      lastStage.current = job.stage;
    }
  }, [job]); // eslint-disable-line react-hooks/exhaustive-deps
  // a failed step explains itself inside the step (ErrorCard), not in a passing toast

  // an object made with a style that's since been deleted keeps it: show it, marked "(deleted)"
  const [gone, setGone] = useState<Style | null>(null);
  useEffect(() => {
    if (!job || !styles.length || styles.some((s) => s.id === job.style.id) || gone?.id === job.style.id) return;
    api.style(job.style.id).then((s) => setGone({ ...s, name: `${s.name} (deleted)` })).catch(() => {});
  }, [job?.style.id, styles]); // eslint-disable-line react-hooks/exhaustive-deps
  const allStyles = job && gone?.id === job.style.id && !styles.some((s) => s.id === gone.id) ? [...styles, gone] : styles;
  const style = (job && allStyles.find((s) => s.id === job.style.id)) ?? styles[0] ?? null;
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

      {job.task?.kind === "auto" && <AutoCard job={job} run={run} />}

      <div className="job">
        <ol className="steps">
          {STEPS.map((s, i) => (
            <li key={s.id} className={stepDone(job, s.id) ? "done" : ""}>
              <button className={step === s.id ? "active" : ""} disabled={!stepEnabled(job, s.id)} onClick={() => setStep(s.id)}>
                <span className="num">{stepDone(job, s.id) ? "✓" : i + 1}</span>
                <span className="t">
                  {s.title}
                  <small>{s.hint}{stepTime(job, s.id) ? ` · ${stepTime(job, s.id) === "reused" ? "reused" : `took ${stepTime(job, s.id)}`}` : ""}</small>
                </span>
              </button>
            </li>
          ))}
        </ol>

        <section className="step-body" aria-live="polite">
          {step === "photo" && <PhotoStep job={job} busy={busy} engines={engines} settings={settings} run={run} styles={allStyles} />}
          {step === "style" && <StyleStep job={job} busy={busy} styles={allStyles} engines={engines} settings={settings} run={run} />}
          {step === "shape" && <ShapeStep job={job} busy={busy} engines={engines} settings={settings} run={run} />}
          {step === "print" && <PrintStep job={job} busy={busy} style={style} settings={settings} run={run} />}
        </section>
      </div>
    </div>
  );
}

const FMT_WORD: Record<string, string> = { stand: "stand toy", magnet: "magnet", keychain: "keychain" };

/** One click ("Make it now"): restyle, 3D shape and print files run as one task; this card follows it. */
function AutoCard({ job, run }: { job: Job; run: (p: Promise<Job>) => void }) {
  const t = job.task;
  const { failed, dismiss } = useFailure(job, "auto");
  const what = FMT_WORD[job.print_params?.format ?? ""] ?? "print";
  if (t && (t.status === "queued" || t.status === "running"))
    return (
      <div className="card panel stack auto-banner" aria-live="polite">
        <strong style={{ fontWeight: 500 }}>Making your {what}</strong>
        <span className="small muted">
          Form Studio is choosing everything for you: the look, the 3D shape and the print files. The steps below fill
          in as it goes, and you can fine-tune any of them afterwards.
        </span>
        <Progress task={t} onCancel={() => run(api.cancel(job.id))} />
      </div>
    );
  if (failed)
    return (
      <ErrorCard
        title={`Couldn't make the ${what} in one go`}
        task={failed}
        onRetry={() => (dismiss(), run(api.autoMake(job.id)))}
        onDismiss={dismiss}
      />
    );
  return null;
}

/** The last failed task of this kind, until it's dismissed or something new starts. */
function useFailure(job: Job, kind: string) {
  const [dismissed, setDismissed] = useState<string | null>(null);
  const t = job.task;
  const key = t ? `${t.id ?? ""}:${t.kind}:${t.error}:${t.elapsed}` : "";
  const failed = t && t.status === "error" && t.kind === kind && dismissed !== key ? t : null;
  return { failed, dismiss: () => setDismissed(key) };
}

/** Another engine that works now: free local first, then cloud, then offline. */
function otherEngine(list: Engines["stylize"] | undefined, current: string): string | undefined {
  const ok = (list ?? []).filter((e) => e.available && e.id !== current);
  return (ok.find((e) => e.kind === "local") ?? ok.find((e) => e.kind === "cloud") ?? ok[0])?.id;
}

/** The preferred engine if it's usable, otherwise the first one that is. */
/** The engines that fit this object: the exact-logo engines only for logos, the rest only for objects. */
function fitKind(list: Engines["stylize"] | undefined, job: Job) {
  return (list ?? []).filter((e) => (job.kind === "logo" ? true : !e.logo_only));
}

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
function PhotoStep({ job, busy, engines, settings, run, styles }: StepProps & { engines: Engines | null; styles: Style[] }) {
  const [engine, setEngine] = useState<string>("");
  const [quality, setQuality] = useState<Quality>(settings?.sdxl_quality ?? "best");
  useEffect(() => {
    if (settings) setQuality(settings.sdxl_quality);
  }, [settings]);
  const [subject, setSubject] = useState(job.subject);
  const [naming, setNaming] = useState(false);
  const toast = useToast();
  useEffect(() => setSubject(job.subject), [job.subject]);
  const changeStyle = (id: string) => {
    if (id === job.style.id) return;
    if (reached(job, "stylized") && !confirm("Switch style? The restyled options and 3D shape will be made again.")) return;
    run(api.setJobStyle(job.id, id));
  };
  const askClaude = async () => {
    setNaming(true);
    try {
      const { subject: s } = await api.suggestName(job.id);
      setSubject(s);
      run(api.rename(job.id, { subject: s }));
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setNaming(false);
    }
  };
  const stylizers = useMemo(() => fitKind(engines?.stylize, job), [engines, job.kind]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setEngine((e) => e || pickAvailable(stylizers, job.kind === "logo" ? "logo" : settings?.engines.stylize)), [settings, engines, stylizers, job.kind]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setEngine(job.kind === "logo" ? "logo" : pickAvailable(stylizers, settings?.engines.stylize)), [job.kind]); // eslint-disable-line react-hooks/exhaustive-deps
  const setKind = (v: boolean) => {
    if (reached(job, "confirmed") && !confirm("Switch? The 3D shape will be made again (your options are kept).")) return;
    run(api.setKind(job.id, v ? "logo" : "object"));
  };
  const working = busy && job.task?.kind === "stylize";
  const { failed, dismiss } = useFailure(job, "stylize");
  const generate = async () => {
    if (subject !== job.subject) await api.rename(job.id, { subject });
    run(api.stylize(job.id, engine, undefined, undefined, engine === "local-sdxl" ? quality : undefined));
  };
  const other = otherEngine(engines?.stylize, engine);

  return (
    <>
      <div className="pair">
        {job.palette?.enabled && job.files.includes("02_reduced.png") ? (
          <figure className="frame" style={{ margin: 0 }}>
            <img src={fileUrl(job, "02_reduced.png")} alt={`Reduced to ${job.palette.colours.length} colours`} />
            <figcaption className="chip">{job.palette.colours.length} colours</figcaption>
          </figure>
        ) : (
          <figure className="frame" style={{ margin: 0 }}>
            {job.input && <img src={fileUrl(job, job.input)} alt="Original photo" />}
            <figcaption className="chip">Original</figcaption>
          </figure>
        )}
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
      {job.kind !== "logo" && <PaletteCard job={job} busy={busy} run={run} />}
      <div className="split">
        <div className="card panel stack">
          <h2>Restyle it</h2>
          <p className="muted small" style={{ margin: 0 }}>
            The style model redraws your object in the chosen style, keeping its shape. You get several options and pick one.
          </p>
          {styles.length > 1 && (
            <div className="field">
              <span>Style</span>
              <StylePicker styles={styles} value={job.style.id} onChange={changeStyle} />
            </div>
          )}
          <label className="field">
            <span>What is the object? (e.g. coffee mug)</span>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input className="input grow" value={subject} onChange={(e) => setSubject(e.target.value)} onBlur={() => subject !== job.subject && run(api.rename(job.id, { subject }))} />
              {settings?.claude.has_key && (
                <button className="btn sm" disabled={naming || busy} onClick={askClaude} title="Let Claude look at the photo and name the object">
                  {naming ? "Looking…" : "Ask Claude"}
                </button>
              )}
            </div>
          </label>
          <span className="small muted">Want a keychain, magnet or stand? Choose that in the Print step.</span>
          <Switch label="Logo or text: keep the exact shapes" checked={job.kind === "logo"} onChange={setKind} disabled={busy} />
          <p className="small muted" style={{ margin: 0 }}>
            {job.kind === "logo"
              ? "The 3D shape is built straight from your file, so every letter and shape stays exactly as drawn, each colour on its own filament. (AI restyles redraw letters and often misspell them.)"
              : "Turn this on for logos, lettering and flat graphics: they're built exactly from the file instead of being redrawn by AI."}
          </p>
          {engines && <EnginePicker engines={stylizers} value={engine} onChange={setEngine} />}
          {engine === "local-sdxl" && <QualityPicker value={quality} onChange={setQuality} disabled={busy} />}
        </div>
        <div className="card panel stack">
          {working && job.task ? (
            <Progress task={job.task} onCancel={() => run(api.cancel(job.id))} />
          ) : (
            <>
              {failed && (
                <ErrorCard
                  title="Couldn't make the options"
                  task={failed}
                  onRetry={() => (dismiss(), generate())}
                  onOther={other ? () => (dismiss(), setEngine(other)) : undefined}
                  onDismiss={dismiss}
                />
              )}
              <button className="btn olive" disabled={busy || !engine} onClick={generate}>
                {engine === "logo" ? "Draw the exact logo (free)" : `Generate ${settings?.candidates ?? 4} option${(settings?.candidates ?? 4) === 1 ? "" : "s"}`}
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

/** Photo step: the picture's colours, and the optional colour reduction (off by default). */
function PaletteCard({ job, busy, run }: { job: Job; busy: boolean; run: (p: Promise<Job>) => void }) {
  const pal = job.palette ?? { enabled: false, max: 4, colours: [] };
  const set = (body: { enabled?: boolean; max_colours?: number; colours?: (string | null)[]; restore?: boolean }) =>
    run(api.setPalette(job.id, { enabled: pal.enabled, ...body }));
  const recolour = (i: number, hex: string | null) => set({ colours: pal.colours.map((_, k) => (k === i ? hex : null)) });
  const edited = pal.colours.some((c) => c.hex.toUpperCase() !== c.original.toUpperCase());
  return (
    <div className="card panel stack" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Colours</h2>
        {pal.enabled && edited && (
          <button className="btn sm" disabled={busy} onClick={() => set({ restore: true })}>Restore all</button>
        )}
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Each colour of the picture prints on its own filament (one AMS slot each). Reduce them to print with fewer filaments: cheaper, with fewer colour changes.
      </p>
      <Switch label="Reduce colours" checked={pal.enabled} onChange={(v) => set({ enabled: v, max_colours: pal.max })} disabled={busy} />
      {pal.enabled ? (
        <>
          <div className="field">
            <span>Keep at most</span>
            <div className="seg wide" role="radiogroup" aria-label="Most colours to keep">
              {[2, 3, 4, 5, 6, 7, 8].map((n) => (
                <button key={n} type="button" role="radio" aria-checked={pal.max === n} className={pal.max === n ? "on" : ""}
                  disabled={busy} onClick={() => set({ max_colours: n })}>
                  {n}
                </button>
              ))}
            </div>
          </div>
          <div className="palette-grid">
            {pal.colours.map((c, i) => (
              <div key={i} className="palette-item">
                <ColourPick label={`Colour ${i + 1}`} colour={{ hex: c.hex, name: c.name }} onPick={(x) => recolour(i, x.hex)} disabled={busy} />
                {c.hex.toUpperCase() !== c.original.toUpperCase() && (
                  <button className="btn sm ghost" disabled={busy} onClick={() => recolour(i, c.original)} title="Back to the picture's own colour">
                    Restore
                  </button>
                )}
              </div>
            ))}
          </div>
          <span className="small muted">
            Restyles start from this preview and keep to these colours; the print uses them too. Turn it off to keep every colour.
          </span>
        </>
      ) : (
        <span className="small muted">Off: every colour in the picture is kept.</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 2. style ----
function StyleStep({ job, busy, styles, engines, settings, run }: StepProps & { styles: Style[]; engines: Engines | null }) {
  const working = busy && job.task?.kind === "stylize";
  const [engine, setEngine] = useState("");
  const [count, setCount] = useState(settings?.candidates ?? 4);
  const [asked, setAsked] = useState<{ before: number; n: number } | null>(null);
  const [zoom, setZoom] = useState<number | null>(null);
  const [quality, setQuality] = useState<Quality>(settings?.sdxl_quality ?? "best");
  useEffect(() => {
    if (settings) setQuality(settings.sdxl_quality);
  }, [settings]);
  const stylizers = useMemo(() => fitKind(engines?.stylize, job), [engines, job.kind]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setEngine((e) => e || pickAvailable(stylizers, job.candidates[job.candidates.length - 1]?.engine ?? (job.kind === "logo" ? "logo" : settings?.engines.stylize))), [engines, settings, stylizers]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!working) setAsked(null);
  }, [working]);
  const engineLabel = (id: string) => engines?.stylize.find((e) => e.id === id)?.label.replace(/ \(.*\)$/, "") ?? id;
  const cands = job.candidates;
  const pending = working && asked ? Math.max(0, asked.n - (cands.length - asked.before)) : 0;
  const chosen = (c: Candidate) => job.confirmed?.file === c.file;

  const { failed, dismiss } = useFailure(job, "stylize");
  const other = otherEngine(engines?.stylize, engine);
  const generate = () => {
    dismiss();
    setAsked({ before: cands.length, n: count });
    run(api.stylize(job.id, engine, undefined, count, engine === "local-sdxl" ? quality : undefined));
  };
  const shapeEngine = pickAvailable(fitKind(engines?.reconstruct, job), job.kind === "logo" ? "logo" : settings?.engines.reconstruct);
  // "3 views": pick this option and draw its front, side and back (stops options still being made)
  const views = (i: number) => run(api.useNow(job.id, i, undefined, true));
  // while options are still being made, "Use this" stops the rest and builds the 3D shape at once
  const use = (i: number) =>
    settings?.views_auto && job.kind !== "logo" ? views(i) : run(working ? api.useNow(job.id, i, shapeEngine || undefined) : api.confirm(job.id, i));
  const remove = (i: number) => run(api.removeCandidate(job.id, i));
  const clear = () => confirm(`Remove all ${cands.length} options?`) && run(api.clearCandidates(job.id));

  useEffect(() => {
    if (zoom === null) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setZoom(null);
      if (e.key === "ArrowRight") setZoom((z) => (z === null ? z : Math.min(cands.length - 1, z + 1)));
      if (e.key === "ArrowLeft") setZoom((z) => (z === null ? z : Math.max(0, z - 1)));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [zoom, cands.length]);

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "end", flexWrap: "nowrap" }}>
        <div className="stack" style={{ gap: 4, flex: 1, minWidth: 0 }}>
          <h2>Pick the one that feels right</h2>
          <span className="muted small">
            {cands.length ? `${cands.length} option${cands.length === 1 ? "" : "s"}. ` : ""}Make as many as you like, from any engine or style, then use one.
          </span>
          {cands.length > 0 && (
            <span className="small muted">
              Next: <b>3 views</b> draws its front, side and back, so the 3D shape gets a real back. Press <b>3 views</b> on an option,
              or <b>Use this</b> and then Make 3 views.
            </span>
          )}
        </div>
        {cands.length > 0 && <button className="btn sm ghost danger" disabled={busy} onClick={clear}>Clear options</button>}
      </div>

      <div className={`card panel generate-bar ${engine === "local-sdxl" ? "with-quality" : ""}`}>
        <label className="field">
          <span>Style</span>
          <select className="select" value={job.style.id} disabled={busy} onChange={(e) => run(api.setJobStyle(job.id, e.target.value))}>
            {styles.map((st) => <option key={st.id} value={st.id}>{st.name}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Engine</span>
          <select className="select" value={engine} disabled={busy} onChange={(e) => setEngine(e.target.value)}>
            {stylizers.map((e) => <option key={e.id} value={e.id} disabled={!e.available}>{e.label}{e.available ? "" : " (not set up)"}</option>)}
          </select>
        </label>
        <label className="field">
          <span>How many</span>
          <select className="select" value={count} disabled={busy} onChange={(e) => setCount(Number(e.target.value))}>
            {[1, 2, 3, 4].map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
        {engine === "local-sdxl" && <QualityPicker value={quality} onChange={setQuality} disabled={busy} />}
        <button className="btn olive" disabled={busy || !engine} onClick={generate}>
          {cands.length ? `Generate ${count} more` : `Generate ${count} option${count === 1 ? "" : "s"}`}
        </button>
      </div>

      {failed && !working && (
        <ErrorCard
          title="Couldn't make the options"
          task={failed}
          onRetry={generate}
          onOther={other ? () => (dismiss(), setEngine(other)) : undefined}
          onDismiss={dismiss}
        />
      )}
      {working && job.task && (
        <div className="card panel stack" style={{ gap: 8 }}>
          <Progress task={job.task} onCancel={() => run(api.cancel(job.id))} cancelLabel="Stop" />
          {cands.length > 0 && <span className="small muted">Like one already? Press <b>Use this now</b> on it: the rest stop and the 3D shape starts straight away.</span>}
        </div>
      )}

      <div className="cands">
        {cands.map((c, i) => (
          <div key={c.file} className={`card cand ${chosen(c) ? "sel" : ""}`}>
            <button className="cand-img" onClick={() => setZoom(i)} aria-label={`View option ${c.n ?? i + 1} large`}>
              <img src={fileUrl(job, c.file)} alt={`Option ${c.n ?? i + 1}`} loading="lazy" />
              {chosen(c) && <span className="chip chosen">Chosen</span>}
            </button>
            <div className="cand-meta">
              <div className="row" style={{ justifyContent: "space-between", gap: 6 }}>
                <strong>Option {c.n ?? i + 1}</strong>
                {c.seed != null && <span className="label">seed {c.seed}</span>}
              </div>
              <span className="small muted cand-sub">
                {c.style_name ?? ""}{c.style_name ? " · " : ""}{engineLabel(c.engine)}{c.quality && c.quality !== "best" ? ` · ${c.quality}` : ""}
              </span>
              <div className="row" style={{ gap: 6 }}>
                <button className="btn sm olive grow" disabled={busy && !working} onClick={() => use(i)} title={working ? "Stops the other options and builds the 3D shape now" : undefined}>
                  {working ? "Use this now →" : chosen(c) ? "Use again" : "Use this →"}
                </button>
                <button className="btn sm ghost" disabled={busy} onClick={() => remove(i)} aria-label={`Remove option ${c.n ?? i + 1}`}>Remove</button>
              </div>
              <button className="btn sm views-btn" disabled={busy && !working} onClick={() => views(i)} title="Use this option and draw its front, side and back">
                3 views · front, side, back
              </button>
            </div>
          </div>
        ))}
        {Array.from({ length: pending }, (_, i) => <div key={`p${i}`} className="skeleton" />)}
      </div>
      {!cands.length && !working && <p className="empty">No options yet. Pick a style and engine, then Generate.</p>}

      {zoom !== null && cands[zoom] && (
        <div className="lightbox" role="dialog" aria-modal="true" onClick={() => setZoom(null)}>
          <div className="lightbox-inner" onClick={(e) => e.stopPropagation()}>
            <img src={fileUrl(job, cands[zoom].file)} alt={`Option ${cands[zoom].n ?? zoom + 1}`} />
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="small">
                <b>Option {cands[zoom].n ?? zoom + 1}</b> · {cands[zoom].style_name} · {engineLabel(cands[zoom].engine)}
              </span>
              <span className="row" style={{ gap: 6 }}>
                <button className="btn sm" disabled={zoom === 0} onClick={() => setZoom(zoom - 1)}>←</button>
                <button className="btn sm" disabled={zoom === cands.length - 1} onClick={() => setZoom(zoom + 1)}>→</button>
                <button className="btn sm" disabled={busy && !working} onClick={() => (views(zoom), setZoom(null))}>3 views</button>
                <button className="btn sm olive" disabled={busy && !working} onClick={() => (use(zoom), setZoom(null))}>{working ? "Use this now →" : "Use this →"}</button>
                <button className="btn sm ghost" onClick={() => setZoom(null)}>Close</button>
              </span>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- 3. shape ----
const VIEW_LABELS = ["Front", "Side", "Back"];

/** Front, side and back of the chosen picture, so multi-view 3D engines see the whole object. */
function ViewsCard({ job, busy, engines, settings, run }: StepProps & { engines: Engines | null }) {
  const [maker, setMaker] = useState("");
  const [zoom, setZoom] = useState<number | null>(null);
  useEffect(() => setMaker((m) => m || pickAvailable(engines?.views, job.views?.engine ?? settings?.engines.views)), [engines, settings]); // eslint-disable-line react-hooks/exhaustive-deps
  const working = busy && job.task?.kind === "views";
  const { failed, dismiss } = useFailure(job, "views");
  const other = otherEngine(engines?.views, maker);
  const files = job.views?.files ?? [];
  const has = files.length === 3;
  const chosen = engines?.views.find((e) => e.id === maker);
  const make = () => (dismiss(), run(api.makeViews(job.id, maker)));

  return (
    <div className="card panel stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3>3 views · front, side, back</h3>
        {has && <span className="chip"><span className="dot" />Ready</span>}
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Draws your chosen look from the front, side and back, so the 3D model sees the whole object instead of guessing the back.
        Meshy, TRELLIS and Hunyuan3D build from all three.
      </p>
      {has && (
        <div className="views">
          {files.map((f, i) => (
            <button key={f} className="view-tile" onClick={() => setZoom(i)} aria-label={`View the ${VIEW_LABELS[i].toLowerCase()} view large`}>
              <img src={fileUrl(job, f)} alt={`${VIEW_LABELS[i]} view`} />
              <span className="chip">{VIEW_LABELS[i]}</span>
            </button>
          ))}
        </div>
      )}
      {working && job.task ? (
        <Progress task={job.task} onCancel={() => run(api.cancel(job.id))} />
      ) : (
        <>
          {failed && (
            <ErrorCard
              title="Couldn't make the views"
              task={failed}
              onRetry={make}
              onOther={other ? () => (dismiss(), setMaker(other)) : undefined}
              onDismiss={dismiss}
            />
          )}
          {engines && !engines.views.some((e) => e.available) && (
            <div className="note info" style={{ display: "block" }}>
              <b>No view maker is set up yet.</b> Any one of these works:
              <ul className="setup-list">
                {engines.views.map((e) => <li key={e.id}><b>{e.label}:</b> {e.reason}</li>)}
              </ul>
              <a className="btn sm" href="#/settings">Set up in Settings</a>
            </div>
          )}
          {engines?.views.some((e) => e.available) && <>
          <label className="field">
            <span>Drawn by</span>
            <select className="select" value={maker} disabled={busy} onChange={(e) => setMaker(e.target.value)}>
              {engines?.views.map((e) => (
                <option key={e.id} value={e.id} disabled={!e.available}>{e.label}{e.available ? "" : " (not set up)"}</option>
              ))}
            </select>
            {chosen && <small className="muted">{chosen.available ? chosen.note : chosen.reason}</small>}
          </label>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn" disabled={busy || !maker || !chosen?.available} onClick={make}>{has ? "Make again" : "Make 3 views"}</button>
            {has && <button className="btn ghost danger" disabled={busy} onClick={() => run(api.removeViews(job.id))}>Remove views</button>}
          </div>
          </>}
        </>
      )}
      {zoom !== null && files[zoom] && (
        <div className="lightbox" role="dialog" aria-modal="true" onClick={() => setZoom(null)}>
          <div className="lightbox-inner" onClick={(e) => e.stopPropagation()}>
            <img src={fileUrl(job, files[zoom])} alt={`${VIEW_LABELS[zoom]} view`} />
            <div className="row" style={{ justifyContent: "space-between" }}>
              <b className="small">{VIEW_LABELS[zoom]} view</b>
              <span className="row" style={{ gap: 6 }}>
                <button className="btn sm" disabled={zoom === 0} onClick={() => setZoom(zoom - 1)}>←</button>
                <button className="btn sm" disabled={zoom === 2} onClick={() => setZoom(zoom + 1)}>→</button>
                <button className="btn sm ghost" onClick={() => setZoom(null)}>Close</button>
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ShapeStep({ job, busy, engines, settings, run }: StepProps & { engines: Engines | null }) {
  const [engine, setEngine] = useState("");
  // logos: only the exact engine fits (AI 3D loses thin letters); objects: everything but it
  const flat = !!job.print_params?.flat_back?.enabled;
  // logos: only the exact engine fits; magnets/keychains/ornaments: "from the picture" first, AI 3D optional
  const list = useMemo(
    () => (engines?.reconstruct ?? []).filter((e) => (job.kind === "logo") === !!e.logo_only && (flat || !e.flat_only)),
    [engines, job.kind, flat],
  );
  useEffect(
    () => setEngine((e) => (e && list.some((x) => x.id === e) ? e : pickAvailable(list, job.reconstruct?.engine ?? (job.kind === "logo" ? "logo" : flat ? "picture" : settings?.engines.reconstruct)))),
    [settings, engines, job.reconstruct, list, job.kind, flat],
  );
  const working = busy && job.task?.kind === "reconstruct";
  const { failed, dismiss } = useFailure(job, "reconstruct");
  const other = otherEngine(engines?.reconstruct, engine);

  return (
    <div className="split">
      <figure className="frame" style={{ margin: 0 }}>
        <img src={fileUrl(job, "03_confirmed.png")} alt="Chosen style" />
        <figcaption className="chip">Chosen look</figcaption>
      </figure>
      <div className="stack">
        {job.kind !== "logo" && <ViewsCard job={job} busy={busy} engines={engines} settings={settings} run={run} />}
        <div className="card panel stack">
          <h2>Build the 3D shape</h2>
          <p className="small muted" style={{ margin: 0 }}>
            {job.kind === "logo"
              ? "Built straight from your artwork: every letter and shape exactly as drawn, each colour its own raised part on a plaque. Instant and free."
              : flat && engine === "picture"
              ? "A flat object is made from the chosen picture itself: its exact outline, a rounded relief, and one part per colour. Instant, and true to the picture. Pick an AI 3D engine instead to take the relief's depth from a 3D model."
              : flat
              ? "The relief's depth comes from this AI 3D model; the outline and colours still come from the picture. \"From the picture\" is faster and usually closer to it."
              : job.views && engines?.reconstruct.find((e) => e.id === engine)?.multi_view
              ? "This engine builds from all 3 views. Form Studio then cleans the shape up for printing."
              : job.views
                ? "This engine uses the front view only. Meshy, TRELLIS and Hunyuan3D use all 3 views."
                : "The 3D model guesses the back and sides from this one image. Make 3 views above for a better back. Form Studio then cleans it up for printing."}
          </p>
          {engines && <EnginePicker engines={list} value={engine} onChange={setEngine} />}
        </div>
        <div className="card panel stack">
          {working && job.task ? (
            <Progress task={job.task} onCancel={() => run(api.cancel(job.id))} />
          ) : (
            <>
              {failed && (
                <ErrorCard
                  title="Couldn't build the 3D shape"
                  task={failed}
                  onRetry={() => (dismiss(), run(api.reconstruct(job.id, engine)))}
                  onOther={other ? () => (dismiss(), setEngine(other)) : undefined}
                  onDismiss={dismiss}
                />
              )}
              <button className="btn olive" disabled={busy || !engine} onClick={() => run(api.reconstruct(job.id, engine))}>
                {reached(job, "reconstructed") ? (engine === "claude-sculpt" ? "Ask Claude again" : "Rebuild shape") : "Build 3D shape"}
              </button>
              {job.files.includes("04_recipe.json") && (
                <>
                  <button className="btn" disabled={busy} onClick={() => run(api.rebuildRecipe(job.id))}>Rebuild from Claude's recipe (free)</button>
                  <p className="small muted" style={{ margin: 0 }}>
                    Claude's recipe is saved with this object ({job.reconstruct && "parts" in job.reconstruct ? `${(job.reconstruct as { parts: number }).parts} parts` : "saved"}). Rebuilding from it gives exactly the same shape.
                  </p>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 4. print ----

/** Before export: how close the print is to the approved picture, and everything printing changed. */
function ReviewCard({ job, busy, onApply }: { job: Job; busy: boolean; onApply: (o: Record<string, unknown>) => void }) {
  const rep = job.report!;
  const f = rep.fidelity ?? {};
  const score = f.score ?? (f.outline_iou != null ? Math.round(f.outline_iou * 1000) / 10 : null);
  const good = score != null && score >= 95;
  const pct = (x?: number) => (x == null ? "–" : `${(x * 100).toFixed(1)}%`);
  const parts = job.parts ?? [];
  return (
    <div className="card panel stack review">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3>Review before printing</h3>
        {score != null && (
          <span className={`chip ${good ? "" : "offline"}`} title="Target: 95% or more">
            <span className="dot" />{score.toFixed(1)}% like the picture
          </span>
        )}
      </div>
      <div className="review-body">
        {job.files.includes("03_confirmed.png") && (
          <img className="review-pic" src={fileUrl(job, "03_confirmed.png")} alt="The approved picture" />
        )}
        <div className="stack" style={{ gap: 8 }}>
          {f.outline_iou != null && (
            <div className="stats">
              <div><span className="label">Outline</span><b>{pct(f.outline_iou)}</b></div>
              <div><span className="label">Colour placement</span><b>{pct(f.colour_match)}</b></div>
            </div>
          )}
          {parts.length > 1 && (
            <div className="row small" style={{ gap: 6, flexWrap: "wrap" }}>
              {parts.map((pt) => (
                <span key={pt.id} className="chip"><i className="swatch" style={{ background: pt.hex }} />AMS {pt.filament} · {pt.name}</span>
              ))}
            </div>
          )}
        </div>
      </div>
      {!!rep.changes?.length && (
        <>
          <span className="small muted">Changed for printing:</span>
          <ul className="changes">
            {rep.changes.map((c) => (
              <li key={c.id}>
                <span className="small">{c.text}</span>
                {c.action && (
                  <button className="btn sm" disabled={busy} onClick={() => onApply(c.action!.params)}>{c.action.label}</button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      <span className="small muted">Change sizes, the finish or any colour on the right; when it looks right, export.</span>
    </div>
  );
}

/** One filament colour: swatch + name; opens a palette of common filaments and a custom colour wheel. */
function ColourPick({ label, colour, onPick, disabled }: { label: string; colour: Colour; onPick: (c: { hex: string; name?: string }) => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState(colour.hex);
  useEffect(() => setCustom(colour.hex), [colour.hex]);
  return (
    <div className="colour-pick">
      <button type="button" className="colour-card" disabled={disabled} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="swatch big" style={{ background: colour.hex }} />
        <span className="colour-text">
          <span className="small muted">{label}</span>
          <b>{colour.name}</b>
        </span>
        <span className={`chev ${open ? "open" : ""}`} aria-hidden>›</span>
      </button>
      {open && (
        <div className="filament-pop">
          <div className="filament-grid" role="listbox" aria-label={`${label} colour`}>
            {FILAMENTS.map((f) => (
              <button key={f.hex} type="button" role="option" aria-selected={f.hex.toUpperCase() === colour.hex.toUpperCase()} title={f.name}
                className={f.hex.toUpperCase() === colour.hex.toUpperCase() ? "on" : ""} style={{ background: f.hex }}
                onClick={() => (onPick(f), setOpen(false))} aria-label={f.name} />
            ))}
          </div>
          <label className="custom-colour">
            <input type="color" value={custom} onChange={(e) => setCustom(e.target.value)} />
            <span className="small">Custom colour</span>
            <button type="button" className="btn sm" disabled={custom.toUpperCase() === colour.hex.toUpperCase()}
              onClick={() => (onPick({ hex: custom }), setOpen(false))}>Use</button>
          </label>
        </div>
      )}
    </div>
  );
}

function ColoursCard({ job, busy, run }: { job: Job; busy: boolean; run: (p: Promise<Job>) => Promise<void> }) {
  const toast = useToast();
  const c = job.colours;
  const parts = job.parts ?? [];
  if (!c || !parts.length) return null;
  const ids = parts.map((p) => p.id);
  const pick = (part: string) => (col: { hex: string; name?: string }) => run(api.setColours(job.id, { [part]: col }));
  const swap = (a: string, b: string) =>
    run(api.setColours(job.id, { [a]: { hex: c[b].hex, name: c[b].name }, [b]: { hex: c[a].hex, name: c[a].name } }));
  const fromPicture = async () => {
    try {
      const s = await api.suggestJobColours(job.id);
      const fit = Object.fromEntries(Object.entries(s).filter(([k]) => ids.includes(k)));
      if (Object.keys(fit).length) await run(api.setColours(job.id, fit));
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  return (
    <div className="card panel stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3>Colours</h3>
        {job.colours_custom ? <span className="small muted">Chosen for this object</span> : (
          <span className="small muted">{job.logo_parts ? "From your artwork" : ids.some((x) => x.startsWith("colour_")) ? "From the picture" : "From the style"}</span>
        )}
      </div>
      <div className={`colours-row ${ids.length === 1 ? "one" : ""}`}>
        {parts.map((part, i) => (
          <div key={part.id} className="colour-slot">
            <ColourPick label={part.id.startsWith("colour_") ? `AMS slot ${part.filament}` : `AMS slot ${part.filament} · ${c[part.id]?.label ?? part.label ?? part.id}`}
              colour={c[part.id] ?? part} onPick={pick(part.id)} disabled={busy} />
            {i < parts.length - 1 && (
              <button type="button" className="swap-mid" disabled={busy} title="Swap these two colours"
                onClick={() => swap(part.id, parts[i + 1].id)} aria-label={`Swap with ${parts[i + 1].label ?? parts[i + 1].id}`}>⇄</button>
            )}
          </div>
        ))}
      </div>
      <div className="row" style={{ gap: 6 }}>
        <button className="btn sm" disabled={busy} onClick={fromPicture}>From the picture</button>
        {job.colours_custom && (
          <button className="btn sm ghost" disabled={busy} onClick={() => run(api.resetColours(job.id))}>
            {job.logo_parts ? "Artwork colours" : "Style colours"}
          </button>
        )}
      </div>
      <span className="small muted">
        The 3MF tells your slicer these colours{ids.length > 1 ? ` for filaments 1–${ids.length}` : ""}. Load the matching filaments in your AMS.
      </span>
    </div>
  );
}
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
  const blender = useBlender();
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
  const parts = job.parts ?? [];
  const multi = parts.length > 1;
  const rawColor = parts[0]?.hex ?? job.colours?.body?.hex ?? style?.palette.body.hex ?? "#5E6B2E";
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
  const finish: Finish = p.geometry.finish ?? "soft";
  return (
    <div className="split">
      <div className="stack">
        <div className="viewer">
          <Suspense fallback={<div className="busy muted">Loading 3D…</div>}>
            <Viewer
              mode={view}
              parts={parts.map((pt) => ({ url: fileUrl(job, pt.file), color: pt.hex }))}
              rawUrl={job.files.includes("04_raw.glb") ? fileUrl(job, "04_raw.glb") : undefined}
              rawColor={rawColor}
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
              {parts.map((pt) => (
                <span key={pt.id}><i className="swatch" style={{ background: pt.hex }} />{pt.name} · AMS slot {pt.filament}</span>
              ))}
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
        {rep && (rep.fidelity || rep.changes) && (
          <ReviewCard job={job} busy={busy || applying} onApply={(o) => apply(o as Overrides)} />
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
          {job.logo_parts && p.logo && (
            <>
              <label className="field" style={{ margin: 0 }}><span>Behind the logo</span></label>
              <div className="seg" role="radiogroup" aria-label="Behind the logo" style={{ alignSelf: "start" }}>
                {BACKERS.filter((b) => b.id !== "bar" || fmt === "stand").map((b) => (
                  <button key={b.id} role="radio" aria-checked={p.logo!.backer === b.id} className={p.logo!.backer === b.id ? "on" : ""}
                    disabled={busy} onClick={() => p.logo!.backer !== b.id && set("logo", { backer: b.id }, true)}>
                    {b.label}
                  </button>
                ))}
              </div>
              <p className="small muted" style={{ margin: 0 }}>{BACKERS.find((b) => b.id === p.logo!.backer)?.hint}</p>
              <Slider label="Logo width" unit=" mm" min={25} max={200} step={1} value={p.logo.width_mm} onChange={(v) => set("logo", { width_mm: v })} onCommit={(v) => set("logo", { width_mm: v }, true)} disabled={busy} />
              <Slider label="Raised letters" unit=" mm" min={0.6} max={6} step={0.2} value={p.logo.raise_mm} onChange={(v) => set("logo", { raise_mm: v })} onCommit={(v) => set("logo", { raise_mm: v }, true)} disabled={busy} />
              <Slider label="Plate thickness" unit=" mm" min={1.5} max={8} step={0.5} value={p.logo.plaque_mm} onChange={(v) => set("logo", { plaque_mm: v })} onCommit={(v) => set("logo", { plaque_mm: v }, true)} disabled={busy} />
              <Slider label="Border" unit=" mm" min={1} max={12} step={0.5} value={p.logo.margin_mm} onChange={(v) => set("logo", { margin_mm: v })} onCommit={(v) => set("logo", { margin_mm: v }, true)} disabled={busy} />
              {p.logo.backer === "plaque" && (
                <Slider label="Corner radius" unit=" mm" min={0} max={12} step={0.5} value={p.logo.corner_mm} onChange={(v) => set("logo", { corner_mm: v })} onCommit={(v) => set("logo", { corner_mm: v }, true)} disabled={busy} />
              )}
              <Slider label="Thinnest stroke" unit=" mm" min={0.6} max={3} step={0.1} value={p.logo.min_stroke_mm} onChange={(v) => set("logo", { min_stroke_mm: v })} onCommit={(v) => set("logo", { min_stroke_mm: v }, true)} disabled={busy} />
            </>
          )}
          {!job.logo_parts && <Slider label={fmt === "stand" ? "Height" : "Object height"} unit=" mm" min={15} max={180} step={1} value={p.geometry.height_mm} onChange={(v) => set("geometry", { height_mm: v })} onCommit={(v) => set("geometry", { height_mm: v }, true)} disabled={busy} />}
          <label className="field">
            <span>3D finish</span>
            <select className="select" value={finish} disabled={busy} onChange={(e) => set("geometry", { finish: e.target.value as Finish }, true)}>
              {FINISHES.filter((f) => !job.logo_parts || !["blender"].includes(f.id)).map((f) => (
                <option key={f.id} value={f.id}>{f.label}{f.id === "blender" && blender && !blender.found ? " (needs Blender)" : ""}</option>
              ))}
            </select>
            <small className="muted">{FINISHES.find((f) => f.id === finish)?.hint}</small>
          </label>
          {finish === "blender" && !job.logo_parts && <NeedsBlender b={blender} />}
          {finish === "blender" && (
            <ClayFields g={p.geometry} disabled={busy} set={(patch, commit) => set("geometry", patch, commit)} />
          )}
          {finish === "voxel" && (
            <Slider label="Block size" unit=" mm" min={1} max={8} step={0.25} value={p.geometry.voxel_size_mm ?? 2.5} onChange={(v) => set("geometry", { voxel_size_mm: v })} onCommit={(v) => set("geometry", { voxel_size_mm: v }, true)} disabled={busy} />
          )}
          {finish === "faceted" && (
            <Slider label="Facets" unit="" min={80} max={2000} step={20} value={p.geometry.facets ?? 400} onChange={(v) => set("geometry", { facets: v })} onCommit={(v) => set("geometry", { facets: v }, true)} disabled={busy} />
          )}
          {finish === "soft" && (
            <Slider label="Softness" unit=" mm" min={0} max={3} step={0.1} value={p.geometry.soften_mm} onChange={(v) => set("geometry", { soften_mm: v })} onCommit={(v) => set("geometry", { soften_mm: v }, true)} disabled={busy} />
          )}
          {!job.logo_parts && <Slider label="Thinnest part" unit=" mm" min={0.8} max={5} step={0.1} value={p.geometry.min_feature_mm} onChange={(v) => set("geometry", { min_feature_mm: v })} onCommit={(v) => set("geometry", { min_feature_mm: v }, true)} disabled={busy} />}
          {!job.logo_parts && <Slider label="Detail (remesh)" unit=" mm" min={0.25} max={1.5} step={0.05} value={p.geometry.voxel_mm} onChange={(v) => set("geometry", { voxel_mm: v })} onCommit={(v) => set("geometry", { voxel_mm: v }, true)} disabled={busy} />}
          <div className="divider" />
          {fmt !== "stand" && !job.logo_parts && (
            <Slider label="Thickness" unit=" mm" min={3} max={25} step={0.5} value={p.flat_back.thickness_mm} onChange={(v) => set("flat_back", { thickness_mm: v })} onCommit={(v) => set("flat_back", { thickness_mm: v }, true)} disabled={busy} />
          )}
          {!!p.flat_back?.enabled && !job.logo_parts && (
            <>
              <Switch
                label="Print in one colour"
                checked={!p.regions?.enabled}
                onChange={(v) => set("regions", { enabled: !v }, true)}
                disabled={busy}
              />
              <span className="small muted">
                {p.regions?.enabled
                  ? `Each of the picture's colours prints as its own part, one AMS slot each${p.regions?.max_colours ? ` (reduced to at most ${p.regions.max_colours} in the Photo step)` : ""}. Change any colour below.`
                  : "One part, one filament. Turn off to print the picture's own colours."}
              </span>
            </>
          )}
          {fmt === "keychain" && (
            <>
              <Slider label="Key-ring hole" unit=" mm" min={3} max={8} step={0.5} value={p.ring.hole_mm} onChange={(v) => set("ring", { hole_mm: v })} onCommit={(v) => set("ring", { hole_mm: v, outer_radius_mm: Math.max(p.ring.outer_radius_mm, v / 2 + 2) }, true)} disabled={busy} />
              <Slider label="Loop thickness" unit=" mm" min={2} max={6} step={0.5} value={p.ring.thickness_mm} onChange={(v) => set("ring", { thickness_mm: v })} onCommit={(v) => set("ring", { thickness_mm: v }, true)} disabled={busy} />
            </>
          )}
          {fmt === "stand" && !job.logo_parts && (
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
          {fmt !== "keychain" && (!job.logo_parts || fmt === "magnet") && (
            <Switch label="Magnet pocket" checked={p.magnet.enabled} onChange={(v) => set("magnet", { enabled: v }, true)} disabled={busy} />
          )}
          {fmt !== "keychain" && (!job.logo_parts || fmt === "magnet") && p.magnet.enabled && (
            <div className="pair" style={{ gap: 10 }}>
              <label className="field"><span>Ø mm</span><input className="input" type="number" step="0.5" min="2" max="30" value={p.magnet.diameter_mm} onChange={(e) => set("magnet", { diameter_mm: Number(e.target.value) })} onBlur={(e) => set("magnet", { diameter_mm: Number(e.target.value) }, true)} /></label>
              <label className="field"><span>Depth mm</span><input className="input" type="number" step="0.5" min="0.5" max="10" value={p.magnet.depth_mm} onChange={(e) => set("magnet", { depth_mm: Number(e.target.value) })} onBlur={(e) => set("magnet", { depth_mm: Number(e.target.value) }, true)} /></label>
            </div>
          )}
        </div>

        <ColoursCard job={job} busy={busy || applying} run={run} />

        <div className="card panel stack">
          <h3>Export</h3>
          <div className="row small">
            {[["3mf", multi ? `3MF (${parts.length} colours)` : "3MF"], ["step", "STEP"]].map(([f, label]) => (
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
                <button className="btn sm ghost" onClick={() => api.open(job.id, "finder", parts[0]?.file ?? "05_print/body.stl").catch((e) => toast(e.message, true))}>Show in Finder</button>
              </div>
              <p className="small muted" style={{ margin: 0 }}>
                {multi
                  ? `The 3MF opens as one object with ${parts.length} parts, each on its own filament (${parts.map((pt) => pt.filament).join(", ")}). Load the matching colours in your AMS.`
                  : "The 3MF opens as one part on filament 1. It lies flat on its back, so it needs no supports."}
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
