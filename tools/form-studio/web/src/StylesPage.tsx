import { useEffect, useMemo, useRef, useState } from "react";
import { api, Finish, FINISHES, Settings, Style, StyleChat, styleChatImageUrl, StyleDetail, StyleEdit, StyleGeometry, styleRefUrl } from "./api";
import { ClayFields, go, NeedsBlender, Slider, useBlender, useToast } from "./ui";

const FINISH_LABEL: Record<Finish, string> = { soft: "Soft", voxel: "Pixel / voxel", faceted: "Low-poly", blender: "Blender clay" };
const MAX_REFS = 4;

// ---------------------------------------------------------------- shared pieces ----

/** The style card thumbnail: up to 3 reference pictures, or its finish (styles have no colours). */
function StyleThumb({ s, srcs }: { s: Pick<Style, "finish">; srcs: string[] }) {
  if (srcs.length) {
    return (
      <div className={`collage n${Math.min(srcs.length, 3)}`}>
        {srcs.slice(0, 3).map((src) => <img key={src} src={src} alt="" loading="lazy" />)}
      </div>
    );
  }
  return (
    <div className="colour-thumb neutral">
      <span className="blob" />
      <span className="chip">{FINISH_LABEL[s.finish ?? "soft"]}</span>
    </div>
  );
}

function FinishFields({ g, onChange, disabled }: { g: StyleGeometry; onChange: (g: StyleGeometry) => void; disabled?: boolean }) {
  const blender = useBlender();
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
      {g.finish === "blender" && <NeedsBlender b={blender} />}
      {g.finish === "blender" && <ClayFields g={g} disabled={disabled} set={(patch) => onChange({ ...g, ...patch })} />}
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
  const [removed, setRemoved] = useState<{ builtins: number; deleted: number }>({ builtins: 0, deleted: 0 });
  const toast = useToast();
  const load = () => {
    api.styles().then(setStyles, (e) => toast(e.message, true));
    api.removedStyles().then(setRemoved).catch(() => {});
  };
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
    if (!confirm(confirmDelete(s))) return;
    try {
      await api.deleteStyle(s.id);
      toast(`${s.name} deleted`);
      load();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const restore = async (which: "builtins" | "deleted") => {
    try {
      const r = await api.restoreStyles(which);
      toast(`${r.restored} style${r.restored === 1 ? "" : "s"} back`);
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
            A style is the look every object gets: shape language, surface, texture and detail, from reference pictures, a 3D finish and a few words. Colours stay with each object.
            Make your own by chatting with ChatGPT or from pictures you like.
          </p>
        </div>
        <a className="btn primary" href="#/styles/new">+ New style</a>
      </div>
      <div className="grid styles-grid">
        <a className="card style-card new-card" href="#/styles/new">
          <span className="plus" aria-hidden="true">+</span>
          <strong>New style</strong>
          <span className="small muted">Chat with ChatGPT, or use your own pictures</span>
        </a>
        {styles?.map((s) => (
          <div key={s.id} className="card style-card">
            <a className="style-thumb" href={`#/styles/${s.id}`} aria-label={`Open ${s.name}`}>
              <StyleThumb s={s} srcs={Array.from({ length: Math.min(s.references, 3) }, (_, i) => styleRefUrl(s, i))} />
            </a>
            <div className="style-meta">
              <div className="style-title">
                <strong title={s.name}>{s.name}</strong>
              </div>
              <p className="clamp2 small muted">{s.description || "No description yet"}</p>
              <div className="style-foot">
                <span className="stage">{s.editable ? `Yours · v${s.version}` : "Built-in"} · {FINISH_LABEL[s.finish ?? "soft"]}</span>
                <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                  <button className="btn sm ghost danger" onClick={() => remove(s)} disabled={styles.length <= 1} title={styles.length <= 1 ? "Keep at least one style" : undefined}>
                    Delete
                  </button>
                  {s.editable ? (
                    <a className="btn sm" href={`#/styles/${s.id}`}>Edit</a>
                  ) : (
                    <button className="btn sm" onClick={() => customize(s.id)} title="Make your own editable copy">Customize</button>
                  )}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
      {(removed.builtins > 0 || removed.deleted > 0) && (
        <div className="restore-bar small muted">
          {removed.builtins > 0 && (
            <span>
              {removed.builtins} built-in style{removed.builtins === 1 ? "" : "s"} hidden ·{" "}
              <button className="link" onClick={() => restore("builtins")}>Restore</button>
            </span>
          )}
          {removed.deleted > 0 && (
            <span>
              {removed.deleted} deleted style{removed.deleted === 1 ? "" : "s"} of yours ·{" "}
              <button className="link" onClick={() => restore("deleted")}>Restore</button>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function confirmDelete(s: Pick<Style, "name" | "editable">) {
  return s.editable
    ? `Delete the style “${s.name}”?\n\nObjects made with it keep working. You can bring it back at the bottom of the Styles page.`
    : `Delete the built-in style “${s.name}”?\n\nIt's hidden from your styles; objects made with it keep working. You can restore built-in styles at the bottom of the Styles page.`;
}

// ---------------------------------------------------------------- new style ----

// A new style starts from nothing but what you give it: no other style's words or finish, and never colours
// (each object keeps its own; they're mapped to filaments in the Print step).
const NEUTRAL_GEOMETRY: StyleGeometry = { height_mm: 50, soften_mm: 0.9, min_feature_mm: 1.6, finish: "soft", voxel_size_mm: 2.5, facets: 400 };

export function NewStyle() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [mode, setMode] = useState<"choose" | "chat" | "form">("choose");
  useEffect(() => {
    api.settings().then(setSettings).catch(() => {});
  }, []);
  const chatReady = !!settings?.openai?.has_key;
  return (
    <div className="page narrow">
      <div className="stack" style={{ gap: 8, marginBottom: 24 }}>
        <a href="#/styles" className="label" style={{ textDecoration: "none" }}>← Styles</a>
        <h1>New style</h1>
        <p className="lede">
          A style is the look: shape language, surface, texture and how much detail. It never sets colours: every object keeps its own, and you map them to filaments when you print.
        </p>
      </div>
      {mode === "choose" && (
        <div className="choice-grid">
          <button className="card panel choice" onClick={() => setMode("chat")}>
            <strong>Create it with ChatGPT</strong>
            <span className="small muted">Describe the look in your own words. ChatGPT asks what it needs, draws a style picture, and you refine it together before saving.</span>
            {!chatReady && settings && <span className="small warn-text">Needs your OpenAI API key (Settings → Cloud accounts).</span>}
          </button>
          <button className="card panel choice" onClick={() => setMode("form")}>
            <strong>Fill it in yourself</strong>
            <span className="small muted">Name it, add reference pictures and choose the 3D finish.</span>
          </button>
        </div>
      )}
      {mode === "chat" && <StyleChatPanel ready={chatReady} onBack={() => setMode("choose")} />}
      {mode === "form" && <NewStyleForm settings={settings} onBack={() => setMode("choose")} />}
    </div>
  );
}

const FINISH_NAME: Record<Finish, string> = { soft: "Soft", voxel: "Pixel / voxel", faceted: "Low-poly", blender: "Blender clay" };

/** New style by conversation with ChatGPT: describe, answer, see the style picture, refine, save. */
function StyleChatPanel({ ready, onBack }: { ready: boolean; onBack: () => void }) {
  const [chat, setChat] = useState<StyleChat | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [name, setName] = useState("");
  const toast = useToast();
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" }); // braces: newer browsers return a promise here, not a cleanup
  }, [chat?.messages.length, busy]);
  useEffect(() => {
    if (chat?.proposal && !name) setName(chat.proposal.name);
  }, [chat?.proposal]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (label: string, p: () => Promise<StyleChat>) => {
    setBusy(label);
    try {
      setChat(await p());
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(null);
    }
  };
  const send = (text: string) => {
    const t = text.trim();
    if (!t) return;
    setMsg("");
    act("ChatGPT is thinking…", () => (chat ? api.styleChatSay(chat.id, t) : api.styleChatStart(t)));
  };
  const save = async () => {
    if (!chat) return;
    setBusy("Saving…");
    try {
      const s = await api.styleChatSave(chat.id, name.trim() || undefined);
      toast(`“${s.name}” is ready to use`);
      go(`/styles/${s.id}`);
    } catch (e) {
      toast((e as Error).message, true);
      setBusy(null);
    }
  };
  const p = chat?.proposal;
  const pic = chat && chat.chosen != null ? chat.images[chat.chosen] : null;
  if (!ready) {
    return (
      <div className="card panel stack">
        <p className="small" style={{ margin: 0 }}>Creating a style with ChatGPT needs your OpenAI API key. Add it in Settings → Cloud accounts → OpenAI, then come back.</p>
        <div className="row"><a className="btn" href="#/settings">Open Settings</a><button className="btn ghost" onClick={onBack}>Back</button></div>
      </div>
    );
  }
  return (
    <div className="style-chat">
      <section className="card panel stack chat-col">
        <div className="chat-log">
          {!chat && (
            <div className="bubble look">
              What look would you like? For example: “chunky wooden toys with visible grain”, “glossy vinyl designer toys”, “knitted plush”. I'll ask a question or two and draw it for you.
            </div>
          )}
          {chat?.messages.map((m, i) => (
            <div key={i} className={`bubble ${m.role === "user" ? "you" : "look"}`}>
              {m.image ? <img src={styleChatImageUrl(chat.id, m.image)} alt="Style picture" /> : m.text}
              {!!m.questions?.length && (
                <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                  {m.questions.map((q) => <span key={q} className="chip">{q}</span>)}
                </div>
              )}
            </div>
          ))}
          {busy && <div className="bubble look skeleton-line">{busy}</div>}
          <div ref={end} />
        </div>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <input className="input grow" autoFocus value={msg} disabled={!!busy}
            placeholder={chat ? "Answer, or say what to change…" : "Describe the look…"}
            onChange={(e) => setMsg(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send(msg)} />
          <button className="btn olive" disabled={!!busy || !msg.trim()} onClick={() => send(msg)}>Send</button>
        </div>
        <button className="btn sm ghost" style={{ alignSelf: "start" }} onClick={onBack} disabled={!!busy}>← Other ways</button>
      </section>

      <section className="card panel stack">
        <span className="label">The style so far</span>
        {p ? (
          <>
            <strong>{p.name}</strong>
            <p className="small" style={{ margin: 0 }}>{p.description}</p>
            <dl className="style-facts small">
              <dt>Shapes</dt><dd>{p.shape_language || "–"}</dd>
              <dt>Surface</dt><dd>{p.surface || "–"}</dd>
              <dt>Texture</dt><dd>{p.texture || "–"}</dd>
              <dt>Detail</dt><dd>{p.detail || "–"}</dd>
              <dt>3D finish</dt><dd>{FINISH_NAME[p.finish]}</dd>
            </dl>
            {pic ? (
              <img className="style-pic" src={styleChatImageUrl(chat!.id, pic)} alt="Style picture" />
            ) : (
              <div className="style-pic empty small muted">No style picture yet</div>
            )}
            {chat!.images.length > 1 && (
              <div className="row" style={{ gap: 6 }}>
                {chat!.images.map((im, i) => (
                  <button key={im} className={`thumb-btn ${chat!.chosen === i ? "on" : ""}`} onClick={() => act("Choosing…", () => api.styleChatChoose(chat!.id, i))}>
                    <img src={styleChatImageUrl(chat!.id, im)} alt={`Picture ${i + 1}`} />
                  </button>
                ))}
              </div>
            )}
            <button className="btn" disabled={!!busy} onClick={() => act("Drawing the style picture…", () => api.styleChatImage(chat!.id))}>
              {chat!.images.length ? "Draw it again" : "Draw the style picture"}
            </button>
            <label className="field">
              <span>Name</span>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <button className="btn olive" disabled={!!busy || !name.trim()} onClick={save}>Save style</button>
            <span className="small muted">Saved with its words, 3D finish and the chosen picture; no colours.</span>
          </>
        ) : (
          <span className="small muted">Describe the look and a draft appears here.</span>
        )}
      </section>
    </div>
  );
}

function NewStyleForm({ settings, onBack }: { settings: Settings | null; onBack: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [geo, setGeo] = useState<StyleGeometry>(NEUTRAL_GEOMETRY);
  const [useClaude, setUseClaude] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const toast = useToast();
  const previews = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files]);
  const addFiles = (fs: File[]) => setFiles([...files, ...fs].slice(0, MAX_REFS));
  const claudeReady = !!settings?.claude.has_key;

  const create = async () => {
    if (!name.trim()) return toast("Give the style a name first.", true);
    try {
      setBusy("Creating…");
      const s = await api.createStyle({ name: name.trim(), description: description.trim(), geometry: geo });
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
            negative_prompt: d.negative_prompt, sculpt_guide: d.sculpt_guide,
            geometry: { ...geo, soften_mm: d.soften_mm, min_feature_mm: d.min_feature_mm },
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
        <p className="small muted" style={{ margin: 0 }}>Pictures of the look you want: material, surface, how chunky. Their objects and colours aren't copied, only the treatment.</p>
        <PicturePicker srcs={previews} onAdd={addFiles} onRemove={(i) => setFiles(files.filter((_, j) => j !== i))} onFirst={(i) => setFiles([files[i], ...files.filter((_, j) => j !== i)])} />
      </section>

      <section className="card panel stack">
        <span className="label">3 · 3D shape</span>
        <FinishFields g={geo} onChange={setGeo} />
      </section>

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
        <button className="btn ghost" onClick={onBack}>Back</button>
        <button className="btn olive" disabled={!!busy || !name.trim()} onClick={create}>{busy ?? "Create style"}</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- editor ----

type Form = Required<Omit<StyleEdit, "geometry" | "based_on">> & {
  geometry: StyleGeometry;
};

const toForm = (s: StyleDetail): Form => ({
  name: s.name,
  description: s.description,
  prompt: s.prompt,
  edit_prompt: s.edit_prompt,
  negative_prompt: s.negative_prompt,
  sculpt_guide: s.sculpt_guide,
  reference_mode: s.reference_mode ?? "style",
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
      setStyle(s); // pictures save straight away; your unsaved words stay as they are
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
        negative_prompt: d.negative_prompt, sculpt_guide: d.sculpt_guide,
        geometry: { ...form.geometry, soften_mm: d.soften_mm, min_feature_mm: d.min_feature_mm, finish: d.finish },
      });
      toast("Claude drafted the style. Review it, then save.");
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setDrafting(false);
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
    if (!confirm(confirmDelete(style))) return;
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
          <span className="chip">{style.removed ? "Deleted" : ro ? "Built-in" : `Yours · v${style.version}`}</span>
          {style.removed ? null : ro ? (
            <>
              <button className="btn sm ghost danger" onClick={remove}>Delete</button>
              <button className="btn primary" onClick={customize}>Customize this style</button>
            </>
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
            <div className="field">
              <span>What local SDXL takes from the first picture</span>
              <div className="seg wide" role="radiogroup" aria-label="What to take from the picture">
                {([["style", "Only the look"], ["full", "Look and layout"]] as const).map(([id, label]) => (
                  <button key={id} type="button" role="radio" aria-checked={form.reference_mode === id} className={form.reference_mode === id ? "on" : ""} disabled={ro} onClick={() => set({ reference_mode: id })}>
                    {label}
                  </button>
                ))}
              </div>
              <small className="muted">
                {form.reference_mode === "style"
                  ? "Material, surface and shape language carry over; the objects (and colours) in the picture don't get copied. Recommended."
                  : "Follows the picture more closely, but can paste its objects (like a small pot or cactus) into your result."}
              </small>
            </div>
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
              <div className="style-thumb"><StyleThumb s={{ finish: form.geometry.finish }} srcs={refs.slice(0, 3)} /></div>
              <div className="style-meta">
                <div className="style-title">
                  <strong>{form.name || "Untitled"}</strong>
                </div>
                <p className="clamp2 small muted">{form.description || "No description yet"}</p>
              </div>
            </div>
          </section>

          {!ro && (
            <section className="card panel stack">
              <h3>Draft with Claude</h3>
              <p className="small muted" style={{ margin: 0 }}>Claude looks at the reference pictures and writes the words, softness and finish (never colours: each object keeps its own). You review before saving.</p>
              <textarea className="input textarea" rows={2} placeholder="Optional notes, e.g. 'make it feel like vintage enamel'" value={notes} onChange={(e) => setNotes(e.target.value)} />
              <button className="btn" disabled={drafting || style.references === 0 || !settings?.claude.has_key} onClick={draft}>
                {drafting ? "Claude is looking…" : "Draft from pictures"}
              </button>
              {!settings?.claude.has_key && <span className="small muted">Add an Anthropic key in Settings to use this.</span>}
              {settings?.claude.has_key && style.references === 0 && <span className="small muted">Add a reference picture first.</span>}
            </section>
          )}

          <p className="small muted" style={{ margin: 0 }}>
            Styles set the look (shapes, surface, texture, detail), never colours. Each object keeps its picture's colours; you map them to filaments in the Print step.
          </p>

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
