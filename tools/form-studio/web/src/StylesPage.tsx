import { useEffect, useMemo, useRef, useState } from "react";
import { api, Colour, Finish, FINISHES, Settings, Style, StyleDetail, StyleEdit, StyleGeometry, styleRefUrl } from "./api";
import { go, Slider, useToast } from "./ui";

const FINISH_LABEL: Record<Finish, string> = { soft: "Soft", voxel: "Pixel / voxel", faceted: "Low-poly" };
const MAX_REFS = 4;

// ---------------------------------------------------------------- shared pieces ----

/** The style card thumbnail: up to 3 reference pictures, or a two-colour block. */
function StyleThumb({ s, srcs }: { s: Pick<Style, "palette" | "finish">; srcs: string[] }) {
  if (srcs.length) {
    return (
      <div className={`collage n${Math.min(srcs.length, 3)}`}>
        {srcs.slice(0, 3).map((src) => <img key={src} src={src} alt="" loading="lazy" />)}
      </div>
    );
  }
  return (
    <div className="colour-thumb" style={{ background: s.palette.base.hex }}>
      <span className="blob" style={{ background: s.palette.body.hex }} />
      <span className="chip">{FINISH_LABEL[s.finish ?? "soft"]}</span>
    </div>
  );
}

function ColourFields({ palette, onChange, disabled }: { palette: { body: Colour; base: Colour }; onChange: (p: { body: Colour; base: Colour }) => void; disabled?: boolean }) {
  return (
    <>
      {(["body", "base"] as const).map((which) => (
        <div key={which} className="colour-row">
          <input type="color" value={palette[which].hex} disabled={disabled} aria-label={`${which} colour`}
            onChange={(e) => onChange({ ...palette, [which]: { ...palette[which], hex: e.target.value.toUpperCase() } })} />
          <label className="field grow">
            <span>{which === "body" ? "Object · filament 1" : "Base · filament 2"}</span>
            <input className="input" value={palette[which].name} readOnly={disabled}
              onChange={(e) => onChange({ ...palette, [which]: { ...palette[which], name: e.target.value } })} />
          </label>
        </div>
      ))}
    </>
  );
}

function FinishFields({ g, onChange, disabled }: { g: StyleGeometry; onChange: (g: StyleGeometry) => void; disabled?: boolean }) {
  return (
    <>
      <div className="field">
        <span>3D finish</span>
        <div className="seg wide" role="radiogroup" aria-label="3D finish">
          {FINISHES.map((f) => (
            <button key={f.id} type="button" role="radio" aria-checked={g.finish === f.id} className={g.finish === f.id ? "on" : ""}
              disabled={disabled} onClick={() => onChange({ ...g, finish: f.id })}>
              {f.label}
            </button>
          ))}
        </div>
        <small className="muted">{FINISHES.find((f) => f.id === g.finish)?.hint} Applied to every object's 3D shape, whichever engine made it.</small>
      </div>
      {g.finish === "voxel" && <Slider label="Block size" unit=" mm" min={1} max={8} step={0.25} value={g.voxel_size_mm} disabled={disabled} onChange={(v) => onChange({ ...g, voxel_size_mm: v })} onCommit={() => {}} />}
      {g.finish === "faceted" && <Slider label="Facets" unit="" min={80} max={2000} step={20} value={g.facets} disabled={disabled} onChange={(v) => onChange({ ...g, facets: v })} onCommit={() => {}} />}
      {g.finish === "soft" && <Slider label="Softness" unit=" mm" min={0} max={3} step={0.1} value={g.soften_mm} disabled={disabled} onChange={(v) => onChange({ ...g, soften_mm: v })} onCommit={() => {}} />}
      <Slider label="Height" unit=" mm" min={15} max={180} step={1} value={g.height_mm} disabled={disabled} onChange={(v) => onChange({ ...g, height_mm: v })} onCommit={() => {}} />
      <Slider label="Thinnest part" unit=" mm" min={0.8} max={5} step={0.1} value={g.min_feature_mm} disabled={disabled} onChange={(v) => onChange({ ...g, min_feature_mm: v })} onCommit={() => {}} />
    </>
  );
}

/** Drop zone + picture tiles. Works for pictures not yet saved (new style) and saved ones (editor). */
function PicturePicker(props: {
  srcs: string[];
  onAdd: (files: File[]) => void;
  onRemove?: (i: number) => void;
  onFirst?: (i: number) => void;
  disabled?: boolean;
}) {
  const { srcs, onAdd, onRemove, onFirst, disabled } = props;
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const room = MAX_REFS - srcs.length;
  const take = (list: FileList | File[] | null) => {
    const files = Array.from(list ?? []).filter((f) => f.type.startsWith("image/"));
    if (files.length) onAdd(files.slice(0, room));
  };
  useEffect(() => {
    if (disabled || room <= 0) return;
    const onPaste = (e: ClipboardEvent) => {
      const files = Array.from(e.clipboardData?.files ?? []);
      if (files.length) take(files);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  });
  return (
    <div className="refs">
      {srcs.map((src, i) => (
        <figure key={src} className="ref-tile">
          <img src={src} alt={`Reference ${i + 1}`} />
          {i === 0 && srcs.length > 1 && <span className="ref-badge">First</span>}
          {!disabled && (
            <div className="ref-actions">
              {onFirst && i > 0 && <button type="button" onClick={() => onFirst(i)} title="Local SDXL uses the first picture">Make first</button>}
              {onRemove && <button type="button" onClick={() => onRemove(i)} aria-label="Remove picture">Remove</button>}
            </div>
          )}
        </figure>
      ))}
      {!disabled && room > 0 && (
        <div
          className={`drop ref-tile ${over ? "over" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => input.current?.click()}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
          onDragOver={(e) => (e.preventDefault(), setOver(true))}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => (e.preventDefault(), setOver(false), take(e.dataTransfer.files))}
        >
          <span className="small muted">+ Add pictures<br />drop, click or paste</span>
          <input ref={input} type="file" accept="image/*" multiple hidden onChange={(e) => (take(e.target.files), (e.target.value = ""))} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- list ----

export function StylesList() {
  const [styles, setStyles] = useState<Style[] | null>(null);
  const toast = useToast();
  const load = () => api.styles().then(setStyles, (e) => toast(e.message, true));
  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const customize = async (id: string) => {
    try {
      go(`/styles/${(await api.duplicateStyle(id)).id}`);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const remove = async (s: Style) => {
    if (!confirm(`Delete the style “${s.name}”? Objects made with it keep their files.`)) return;
    try {
      await api.deleteStyle(s.id);
      load();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  return (
    <div className="page">
      <div className="lib-head">
        <div className="stack" style={{ gap: 8 }}>
          <span className="label">Styles</span>
          <h1>Your house styles</h1>
          <p className="lede">
            A style is the look every object gets: reference pictures, two print colours, a 3D finish and a few words.
            Make your own from pictures you like.
          </p>
        </div>
        <a className="btn primary" href="#/styles/new">+ New style</a>
      </div>
      <div className="grid styles-grid">
        <a className="card style-card new-card" href="#/styles/new">
          <span className="plus" aria-hidden="true">+</span>
          <strong>New style</strong>
          <span className="small muted">From your own reference pictures</span>
        </a>
        {styles?.map((s) => (
          <div key={s.id} className="card style-card">
            <a className="style-thumb" href={`#/styles/${s.id}`} aria-label={`Open ${s.name}`}>
              <StyleThumb s={s} srcs={Array.from({ length: Math.min(s.references, 3) }, (_, i) => styleRefUrl(s, i))} />
            </a>
            <div className="style-meta">
              <div className="style-title">
                <strong title={s.name}>{s.name}</strong>
                <span className="swatches">
                  {Object.values(s.palette).map((c) => <span key={c.hex + c.name} className="swatch" style={{ background: c.hex }} title={c.name} />)}
                </span>
              </div>
              <p className="clamp2 small muted">{s.description || "No description yet"}</p>
              <div className="style-foot">
                <span className="stage">{s.editable ? `Yours · v${s.version}` : "Built-in"} · {FINISH_LABEL[s.finish ?? "soft"]}</span>
                <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                  {s.editable ? (
                    <>
                      <button className="btn sm ghost danger" onClick={() => remove(s)}>Delete</button>
                      <a className="btn sm" href={`#/styles/${s.id}`}>Edit</a>
                    </>
                  ) : (
                    <button className="btn sm" onClick={() => customize(s.id)} title="Make your own editable copy">Customize</button>
                  )}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- new style ----

export function NewStyle() {
  const [styles, setStyles] = useState<Style[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [basedOn, setBasedOn] = useState("clay-minimal");
  const [palette, setPalette] = useState<{ body: Colour; base: Colour }>({ body: { name: "Olive", hex: "#5E6B2E" }, base: { name: "Bone White", hex: "#F2F0EA" } });
  const [geo, setGeo] = useState<StyleGeometry | null>(null);
  const [useClaude, setUseClaude] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const toast = useToast();
  const previews = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files]);

  useEffect(() => {
    api.styles().then(setStyles).catch(() => {});
    api.settings().then(setSettings).catch(() => {});
  }, []);
  // starting style sets the finish and shape defaults (and colours, until you pick your own)
  const [coloursTouched, setColoursTouched] = useState(false);
  useEffect(() => {
    api.style(basedOn).then((s) => {
      setGeo({ ...s.geometry, finish: s.geometry.finish ?? "soft", voxel_size_mm: s.geometry.voxel_size_mm ?? 2.5, facets: s.geometry.facets ?? 400 });
      if (!coloursTouched) setPalette({ body: { ...s.palette.body }, base: { ...s.palette.base } });
    }).catch(() => {});
  }, [basedOn]); // eslint-disable-line react-hooks/exhaustive-deps

  const suggest = async (fs = files) => {
    if (!fs.length) return;
    try {
      setPalette(await api.suggestColours({ files: fs }));
      setColoursTouched(true);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const addFiles = (fs: File[]) => {
    const next = [...files, ...fs].slice(0, MAX_REFS);
    setFiles(next);
    if (!coloursTouched) suggest(next); // first pictures: suggest colours straight away
  };
  const claudeReady = !!settings?.claude.has_key;

  const create = async () => {
    if (!name.trim()) return toast("Give the style a name first.", true);
    try {
      setBusy("Creating…");
      const s = await api.createStyle({ name: name.trim(), description: description.trim(), based_on: basedOn, palette, geometry: geo ?? undefined });
      if (files.length) {
        setBusy(`Uploading ${files.length} picture${files.length === 1 ? "" : "s"}…`);
        await api.addReferences(s.id, files);
      }
      if (files.length && claudeReady && useClaude) {
        setBusy("Claude is writing the style…");
        try {
          const d = await api.draftStyle(s.id, description);
          await api.updateStyle(s.id, {
            name: name.trim(), description: description.trim() || d.description, prompt: d.prompt, edit_prompt: d.edit_prompt,
            negative_prompt: d.negative_prompt, sculpt_guide: d.sculpt_guide, palette,
            geometry: geo ? { ...geo, soften_mm: d.soften_mm, min_feature_mm: d.min_feature_mm } : undefined,
          });
        } catch (e) {
          toast(`Style created; Claude couldn't draft it: ${(e as Error).message}`, true);
        }
      }
      go(`/styles/${s.id}`);
    } catch (e) {
      toast((e as Error).message, true);
      setBusy(null);
    }
  };

  return (
    <div className="page narrow">
      <div className="stack" style={{ gap: 8, marginBottom: 24 }}>
        <a href="#/styles" className="label" style={{ textDecoration: "none" }}>← Styles</a>
        <h1>New style</h1>
        <p className="lede">Show Form Studio what you like. Your pictures steer how every object is redrawn; the finish shapes the print.</p>
      </div>

      <div className="stack" style={{ gap: 20 }}>
        <section className="card panel stack">
          <span className="label">1 · Name</span>
          <label className="field">
            <span>Name</span>
            <input className="input" autoFocus placeholder="e.g. Glossy toy" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>What should it feel like? (optional)</span>
            <input className="input" placeholder="e.g. shiny vinyl designer toys, bold and rounded" value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
        </section>

        <section className="card panel stack">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="label">2 · Reference pictures</span>
            <span className="small muted">{files.length} of {MAX_REFS}</span>
          </div>
          <p className="small muted" style={{ margin: 0 }}>Pictures of the look you want: material, colours, how chunky. Objects in them aren't copied, only the style.</p>
          <PicturePicker srcs={previews} onAdd={addFiles} onRemove={(i) => setFiles(files.filter((_, j) => j !== i))} onFirst={(i) => setFiles([files[i], ...files.filter((_, j) => j !== i)])} />
        </section>

        <section className="card panel stack">
          <span className="label">3 · Start from</span>
          <p className="small muted" style={{ margin: 0 }}>Your style begins as a copy of this one's words, finish and shape settings.</p>
          <div className="pick-grid">
            {styles.map((s) => (
              <button key={s.id} type="button" className={`pick ${basedOn === s.id ? "on" : ""}`} onClick={() => setBasedOn(s.id)}>
                <span className="pick-thumb"><StyleThumb s={s} srcs={s.references ? [styleRefUrl(s, 0)] : []} /></span>
                <span className="pick-name">{s.name}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="card panel stack">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="label">4 · Print colours</span>
            <button className="btn sm" disabled={!files.length} onClick={() => suggest()} title={files.length ? "" : "Add pictures first"}>Suggest from pictures</button>
          </div>
          <ColourFields palette={palette} onChange={(p) => (setPalette(p), setColoursTouched(true))} />
        </section>

        {geo && (
          <section className="card panel stack">
            <span className="label">5 · 3D shape</span>
            <FinishFields g={geo} onChange={setGeo} />
          </section>
        )}

        {claudeReady && (
          <label className="switch card panel">
            <span>
              <strong style={{ fontWeight: 500 }}>Let Claude write the style words from my pictures</strong>
              <br />
              <span className="small muted">The restyle instructions and sculpt guide. You can edit them afterwards. About 1¢.</span>
            </span>
            <input type="checkbox" checked={useClaude && files.length > 0} disabled={!files.length} onChange={(e) => setUseClaude(e.target.checked)} />
          </label>
        )}

        <div className="row" style={{ justifyContent: "flex-end" }}>
          <a className="btn ghost" href="#/styles">Cancel</a>
          <button className="btn olive" disabled={!!busy || !name.trim()} onClick={create}>{busy ?? "Create style"}</button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- editor ----

type Form = Required<Omit<StyleEdit, "palette" | "geometry" | "based_on">> & {
  palette: NonNullable<StyleEdit["palette"]>;
  geometry: StyleGeometry;
};

const toForm = (s: StyleDetail): Form => ({
  name: s.name,
  description: s.description,
  prompt: s.prompt,
  edit_prompt: s.edit_prompt,
  negative_prompt: s.negative_prompt,
  sculpt_guide: s.sculpt_guide,
  palette: { body: { ...s.palette.body }, base: { ...s.palette.base } },
  geometry: {
    height_mm: s.geometry.height_mm ?? 50,
    soften_mm: s.geometry.soften_mm ?? 0.9,
    min_feature_mm: s.geometry.min_feature_mm ?? 1.6,
    finish: s.geometry.finish ?? "soft",
    voxel_size_mm: s.geometry.voxel_size_mm ?? 2.5,
    facets: s.geometry.facets ?? 400,
  },
});

export function StyleEditor({ id }: { id: string }) {
  const [style, setStyle] = useState<StyleDetail | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [dirty, setDirty] = useState(false);
  const [notes, setNotes] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api.style(id).then((s) => (setStyle(s), setForm(toForm(s))), (e) => toast(e.message, true));
    api.settings().then(setSettings).catch(() => {});
  }, [id, toast]);

  // don't lose unsaved changes by leaving the page
  useEffect(() => {
    if (!dirty) return;
    const before = (e: BeforeUnloadEvent) => e.preventDefault();
    const leave = (e: HashChangeEvent) => {
      if (!confirm("You have unsaved changes to this style. Leave without saving?")) {
        history.replaceState(null, "", e.oldURL.slice(e.oldURL.indexOf("#")));
        e.stopImmediatePropagation();
      }
    };
    window.addEventListener("beforeunload", before);
    window.addEventListener("hashchange", leave, true);
    return () => (window.removeEventListener("beforeunload", before), window.removeEventListener("hashchange", leave, true));
  }, [dirty]);

  if (!style || !form) return <div className="page muted">Loading…</div>;
  const ro = !style.editable;
  const set = (patch: Partial<Form>) => (setForm({ ...form, ...patch }), setDirty(true));
  const refs = Array.from({ length: style.references }, (_, i) => styleRefUrl(style, i));

  const save = async () => {
    setSaving(true);
    try {
      const s = await api.updateStyle(style.id, form);
      setStyle(s);
      setForm(toForm(s));
      setDirty(false);
      toast(`Saved · version ${s.version}`);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setSaving(false);
    }
  };
  const refsCall = async (fn: () => Promise<StyleDetail>) => {
    try {
      const s = await fn();
      setStyle(s); // pictures save straight away; your unsaved words and colours stay as they are
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const draft = async () => {
    setDrafting(true);
    try {
      const d = await api.draftStyle(style.id, notes);
      set({
        description: form.description || d.description, prompt: d.prompt, edit_prompt: d.edit_prompt,
        negative_prompt: d.negative_prompt, sculpt_guide: d.sculpt_guide, palette: { body: d.body, base: d.base },
        geometry: { ...form.geometry, soften_mm: d.soften_mm, min_feature_mm: d.min_feature_mm, finish: d.finish },
      });
      toast("Claude drafted the style. Review it, then save.");
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setDrafting(false);
    }
  };
  const suggest = async () => {
    try {
      const p = await api.suggestColours({ style: style.id });
      set({ palette: p });
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const customize = async () => {
    try {
      go(`/styles/${(await api.duplicateStyle(style.id)).id}`);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const remove = async () => {
    if (!confirm(`Delete the style “${style.name}”? Objects made with it keep their files.`)) return;
    try {
      setDirty(false);
      await api.deleteStyle(style.id);
      go("/styles");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const text = (key: "prompt" | "edit_prompt" | "negative_prompt" | "sculpt_guide", label: string, hint: string, rows = 3) => (
    <label className="field">
      <span>{label}</span>
      <textarea className="input textarea" rows={rows} value={form[key]} readOnly={ro} onChange={(e) => set({ [key]: e.target.value })} />
      <small className="muted">{hint}</small>
    </label>
  );

  return (
    <div className="page with-savebar">
      <div className="job-head">
        <div className="stack" style={{ gap: 4, minWidth: 0 }}>
          <a href="#/styles" className="label" style={{ textDecoration: "none" }}>← Styles</a>
          <input className="name" value={form.name} readOnly={ro} size={Math.max(6, Math.min(form.name.length, 28))} aria-label="Style name" onChange={(e) => set({ name: e.target.value })} />
        </div>
        <div className="row">
          <span className="chip">{ro ? "Built-in" : `Yours · v${style.version}`}</span>
          {ro ? (
            <button className="btn primary" onClick={customize}>Customize this style</button>
          ) : (
            <>
              <button className="btn sm" onClick={customize}>Duplicate</button>
              <button className="btn sm ghost danger" onClick={remove}>Delete</button>
            </>
          )}
        </div>
      </div>
      {ro && <div className="note info" style={{ marginBottom: 20 }}>Built-in styles can't be changed. “Customize this style” makes your own copy you can edit.</div>}

      <div className="split">
        <div className="stack" style={{ gap: 20, minWidth: 0 }}>
          <section className="card panel stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3>Look · reference pictures</h3>
              <span className="small muted">{style.references} of {MAX_REFS}</span>
            </div>
            <p className="small muted" style={{ margin: 0 }}>
              ChatGPT, Nano Banana and Claude look at all of them; local SDXL uses the first. Pictures save as soon as you add them.
            </p>
            <PicturePicker
              srcs={refs}
              disabled={ro}
              onAdd={(files) => refsCall(() => api.addReferences(style.id, files))}
              onRemove={(i) => refsCall(() => api.removeReference(style.id, i))}
              onFirst={(i) => refsCall(() => api.makeReferenceFirst(style.id, i))}
            />
          </section>

          <section className="card panel stack">
            <h3>Words</h3>
            <label className="field">
              <span>Description</span>
              <input className="input" value={form.description} readOnly={ro} onChange={(e) => set({ description: e.target.value })} />
            </label>
            {text("edit_prompt", "Restyle instruction", "Sent to ChatGPT, Nano Banana and FLUX with your photo. Keep {subject} where the object's name goes.")}
            {text("prompt", "Image prompt", "Used by local SDXL. Keep {subject} where the object's name goes.")}
            {text("negative_prompt", "Avoid", "Things the image should not have.", 2)}
            {text("sculpt_guide", "Sculpt guide", "How Claude Sculpt should build any object in this style.")}
          </section>
        </div>

        <div className="stack" style={{ gap: 20 }}>
          <section className="card panel stack">
            <span className="label">Preview</span>
            <div className="card style-card preview">
              <div className="style-thumb"><StyleThumb s={{ palette: form.palette, finish: form.geometry.finish }} srcs={refs.slice(0, 3)} /></div>
              <div className="style-meta">
                <div className="style-title">
                  <strong>{form.name || "Untitled"}</strong>
                  <span className="swatches">{Object.values(form.palette).map((c, i) => <span key={i} className="swatch" style={{ background: c.hex }} />)}</span>
                </div>
                <p className="clamp2 small muted">{form.description || "No description yet"}</p>
              </div>
            </div>
          </section>

          {!ro && (
            <section className="card panel stack">
              <h3>Draft with Claude</h3>
              <p className="small muted" style={{ margin: 0 }}>Claude looks at the reference pictures and writes the words, colours, softness and finish. You review before saving.</p>
              <textarea className="input textarea" rows={2} placeholder="Optional notes, e.g. 'make it feel like vintage enamel'" value={notes} onChange={(e) => setNotes(e.target.value)} />
              <button className="btn" disabled={drafting || style.references === 0 || !settings?.claude.has_key} onClick={draft}>
                {drafting ? "Claude is looking…" : "Draft from pictures"}
              </button>
              {!settings?.claude.has_key && <span className="small muted">Add an Anthropic key in Settings to use this.</span>}
              {settings?.claude.has_key && style.references === 0 && <span className="small muted">Add a reference picture first.</span>}
            </section>
          )}

          <section className="card panel stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3>Print colours</h3>
              {!ro && <button className="btn sm" disabled={!style.references} onClick={suggest}>Suggest from pictures</button>}
            </div>
            <ColourFields palette={form.palette} disabled={ro} onChange={(p) => set({ palette: p })} />
          </section>

          <section className="card panel stack">
            <h3>3D shape</h3>
            <FinishFields g={form.geometry} disabled={ro} onChange={(g) => set({ geometry: g })} />
          </section>
        </div>
      </div>

      {dirty && !ro && (
        <div className="savebar" role="region" aria-label="Unsaved changes">
          <span>Unsaved changes to <b>{form.name}</b></span>
          <span className="row">
            <button className="btn ghost" disabled={saving} onClick={() => (setForm(toForm(style)), setDirty(false))}>Discard</button>
            <button className="btn olive" disabled={saving} onClick={save}>{saving ? "Saving…" : "Save style"}</button>
          </span>
        </div>
      )}
    </div>
  );
}
