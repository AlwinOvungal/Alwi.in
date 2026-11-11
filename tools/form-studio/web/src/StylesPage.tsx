import { useEffect, useRef, useState } from "react";
import { api, Settings, Style, StyleDetail, StyleEdit, styleRefUrl } from "./api";
import { go, Slider, useToast } from "./ui";

// ---------------------------------------------------------------- list ----
export function StylesList() {
  const [styles, setStyles] = useState<Style[] | null>(null);
  const [name, setName] = useState("");
  const toast = useToast();
  useEffect(() => {
    api.styles().then(setStyles, (e) => toast(e.message, true));
  }, [toast]);

  const create = async () => {
    try {
      const s = await api.createStyle({ name: name.trim() || "My style", description: "" });
      go(`/styles/${s.id}`);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const duplicate = async (id: string) => {
    try {
      const s = await api.duplicateStyle(id);
      go(`/styles/${s.id}`);
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
            A style is the look every object gets: reference pictures, two print colours and a few words. Make your own from pictures you
            like; Claude can write the words for you.
          </p>
        </div>
        <div className="card panel stack" style={{ minWidth: 300 }}>
          <label className="field">
            <span>New style</span>
            <input className="input" placeholder="e.g. Glossy toy" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} />
          </label>
          <button className="btn primary" onClick={create}>Create style</button>
        </div>
      </div>
      <div className="grid">
        {styles?.map((s) => (
          <div key={s.id} className="card tile">
            <a className="thumb" href={`#/styles/${s.id}`} style={{ textDecoration: "none" }}>
              {s.references > 0 ? (
                <img src={styleRefUrl(s, 0)} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
              ) : (
                <div className="swatch-block">
                  <span style={{ background: s.palette.body.hex }} />
                  <span style={{ background: s.palette.base.hex }} />
                </div>
              )}
            </a>
            <div className="meta">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>{s.name}</strong>
                <span className="swatches">
                  {Object.values(s.palette).map((c) => <span key={c.hex + c.name} className="swatch" style={{ background: c.hex }} title={c.name} />)}
                </span>
              </div>
              <span className="small muted">{s.description || "No description yet"}</span>
              <div className="row" style={{ marginTop: 6 }}>
                <span className="stage">{s.editable ? `Yours · v${s.version}` : `Built-in · v${s.version}`}</span>
                <span className="grow" />
                {s.editable ? (
                  <a className="btn sm" href={`#/styles/${s.id}`}>Edit</a>
                ) : (
                  <button className="btn sm" onClick={() => duplicate(s.id)}>Duplicate</button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- editor ----
type Form = Required<Omit<StyleEdit, "palette" | "geometry">> & {
  palette: NonNullable<StyleEdit["palette"]>;
  geometry: NonNullable<StyleEdit["geometry"]>;
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
  },
});

export function StyleEditor({ id }: { id: string }) {
  const [style, setStyle] = useState<StyleDetail | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [dirty, setDirty] = useState(false);
  const [notes, setNotes] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [over, setOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const toast = useToast();

  useEffect(() => {
    api.style(id).then((s) => (setStyle(s), setForm(toForm(s))), (e) => toast(e.message, true));
    api.settings().then(setSettings).catch(() => {});
  }, [id, toast]);

  if (!style || !form) return <div className="page muted">Loading…</div>;
  const ro = !style.editable;
  const set = (patch: Partial<Form>) => (setForm({ ...form, ...patch }), setDirty(true));
  const colour = (which: "body" | "base", patch: Partial<Form["palette"]["body"]>) =>
    set({ palette: { ...form.palette, [which]: { ...form.palette[which], ...patch } } });

  const save = async () => {
    try {
      const s = await api.updateStyle(style.id, form);
      setStyle(s);
      setForm(toForm(s));
      setDirty(false);
      toast(`Saved · version ${s.version}`);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const addRefs = async (files: FileList | null) => {
    if (!files) return;
    let s = style;
    for (const f of Array.from(files).slice(0, 4 - style.references)) {
      try {
        s = await api.addReference(style.id, f);
      } catch (e) {
        toast((e as Error).message, true);
      }
    }
    setStyle({ ...s });
  };
  const draft = async () => {
    setDrafting(true);
    try {
      const d = await api.draftStyle(style.id, notes);
      set({
        name: form.name === "My style" || !form.name ? d.name : form.name,
        description: d.description,
        prompt: d.prompt,
        edit_prompt: d.edit_prompt,
        negative_prompt: d.negative_prompt,
        sculpt_guide: d.sculpt_guide,
        palette: { body: d.body, base: d.base },
        geometry: { ...form.geometry, soften_mm: d.soften_mm, min_feature_mm: d.min_feature_mm },
      });
      toast("Claude drafted the style. Review it, then save.");
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setDrafting(false);
    }
  };
  const remove = async () => {
    if (!confirm(`Delete the style “${style.name}”? Objects made with it keep their files.`)) return;
    try {
      await api.deleteStyle(style.id);
      go("/styles");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const duplicate = async () => {
    try {
      const s = await api.duplicateStyle(style.id);
      go(`/styles/${s.id}`);
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
    <div className="page">
      <div className="job-head">
        <div className="stack" style={{ gap: 4 }}>
          <a href="#/styles" className="label" style={{ textDecoration: "none" }}>← Styles</a>
          <input className="name" value={form.name} readOnly={ro} size={Math.max(6, form.name.length)} aria-label="Style name" onChange={(e) => set({ name: e.target.value })} />
        </div>
        <div className="row">
          <span className="chip">{ro ? "Built-in" : "Yours"} · v{style.version}</span>
          <button className="btn sm" onClick={duplicate}>Duplicate</button>
          {!ro && <button className="btn sm ghost danger" onClick={remove}>Delete</button>}
          {!ro && <button className="btn olive" disabled={!dirty} onClick={save}>{dirty ? "Save style" : "Saved"}</button>}
        </div>
      </div>
      {ro && <div className="note info" style={{ marginBottom: 20 }}>Built-in styles can't be changed. Duplicate this one to make your own version.</div>}

      <div className="split">
        <div className="stack" style={{ gap: 20 }}>
          <div className="card panel stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3>Reference pictures</h3>
              <span className="small muted">{style.references} of 4</span>
            </div>
            <p className="small muted" style={{ margin: 0 }}>
              Pictures that show the look you want: material, colours, how chunky. Nano Banana and Claude look at all of them; local SDXL uses the first.
            </p>
            <div className="refs">
              {Array.from({ length: style.references }, (_, i) => (
                <figure key={`${style.version}-${i}`} className="ref-tile">
                  <img src={styleRefUrl(style, i)} alt={`Reference ${i + 1}`} />
                  {!ro && (
                    <button className="ref-x" aria-label="Remove" onClick={async () => setStyle(await api.removeReference(style.id, i))}>×</button>
                  )}
                </figure>
              ))}
              {!ro && style.references < 4 && (
                <div
                  className={`drop ref-tile ${over ? "over" : ""}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => fileInput.current?.click()}
                  onDragOver={(e) => (e.preventDefault(), setOver(true))}
                  onDragLeave={() => setOver(false)}
                  onDrop={(e) => (e.preventDefault(), setOver(false), addRefs(e.dataTransfer.files))}
                >
                  <span className="small muted">+ Add picture</span>
                  <input ref={fileInput} type="file" accept="image/*" multiple hidden onChange={(e) => addRefs(e.target.files)} />
                </div>
              )}
            </div>
          </div>

          <div className="card panel stack">
            <h3>Words</h3>
            <label className="field">
              <span>Description</span>
              <input className="input" value={form.description} readOnly={ro} onChange={(e) => set({ description: e.target.value })} />
            </label>
            {text("edit_prompt", "Restyle instruction", "Sent to cloud image models with your photo. Keep {subject} where the object's name goes.")}
            {text("prompt", "Image prompt", "Used by local SDXL. Keep {subject} where the object's name goes.")}
            {text("negative_prompt", "Avoid", "Things the image should not have.", 2)}
            {text("sculpt_guide", "Sculpt guide", "How Claude Sculpt should build any object in this style.")}
          </div>
        </div>

        <div className="stack">
          {!ro && (
            <div className="card panel stack">
              <h3>Draft with Claude</h3>
              <p className="small muted" style={{ margin: 0 }}>
                Claude looks at your reference pictures and writes the words, colours and softness. You review before saving.
              </p>
              <textarea className="input textarea" rows={2} placeholder="Optional notes, e.g. 'make it feel like vintage enamel'" value={notes} onChange={(e) => setNotes(e.target.value)} />
              <button className="btn" disabled={drafting || style.references === 0 || !settings?.claude.has_key} onClick={draft}>
                {drafting ? "Claude is looking…" : "Draft from references"}
              </button>
              {!settings?.claude.has_key && <span className="small muted">Add an Anthropic key in Settings to use this.</span>}
              {settings?.claude.has_key && style.references === 0 && <span className="small muted">Add a reference picture first.</span>}
            </div>
          )}

          <div className="card panel stack">
            <h3>Print colours</h3>
            {(["body", "base"] as const).map((which) => (
              <div key={which} className="colour-row">
                <input type="color" value={form.palette[which].hex} disabled={ro} aria-label={`${which} colour`} onChange={(e) => colour(which, { hex: e.target.value.toUpperCase() })} />
                <label className="field grow">
                  <span>{which === "body" ? "Object · filament 1" : "Base · filament 2"}</span>
                  <input className="input" value={form.palette[which].name} readOnly={ro} onChange={(e) => colour(which, { name: e.target.value })} />
                </label>
              </div>
            ))}
          </div>

          <div className="card panel stack">
            <h3>Shape defaults</h3>
            <Slider label="Height" unit=" mm" min={15} max={180} step={1} value={form.geometry.height_mm} disabled={ro} onChange={(v) => set({ geometry: { ...form.geometry, height_mm: v } })} onCommit={() => {}} />
            <Slider label="Softness" unit=" mm" min={0} max={3} step={0.1} value={form.geometry.soften_mm} disabled={ro} onChange={(v) => set({ geometry: { ...form.geometry, soften_mm: v } })} onCommit={() => {}} />
            <Slider label="Thinnest part" unit=" mm" min={0.8} max={5} step={0.1} value={form.geometry.min_feature_mm} disabled={ro} onChange={(v) => set({ geometry: { ...form.geometry, min_feature_mm: v } })} onCommit={() => {}} />
          </div>
        </div>
      </div>
    </div>
  );
}
