import { useEffect, useRef, useState } from "react";
import { api, fileUrl, Job, Settings, Style, styleRefUrl } from "./api";
import { go, timeAgo, useToast } from "./ui";

const STAGE_LABEL: Record<string, string> = {
  new: "New",
  cutout: "Photo ready",
  stylized: "Pick a style",
  confirmed: "Style picked",
  reconstructed: "Shape built",
  printified: "Ready to export",
  exported: "Exported",
};

export default function Library() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [styles, setStyles] = useState<Style[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const toast = useToast();

  useEffect(() => {
    api.jobs().then(setJobs, (e) => toast(e.message, true));
    api.styles().then(setStyles).catch(() => {});
    api.settings().then(setSettings).catch(() => {});
  }, [toast]);
  const styleName = (id: string, fallback?: string) => styles.find((s) => s.id === id)?.name ?? fallback ?? id;

  return (
    <div className="page">
      <div className="lib-head">
        <div className="stack" style={{ gap: 8 }}>
          <span className="label">Photo → house style → print</span>
          <h1>Your objects</h1>
          <p className="lede">
            Drop in a photo of anything. Form Studio restyles it in your house style, turns it into a magnet,
            keychain, ornament or figurine, and prepares a multi-colour print file for your AMS.
          </p>
        </div>
        <a className="card panel ref" href="#/styles" style={{ maxWidth: 360, textDecoration: "none" }}>
          <span className="swatch-stack">
            {styles.slice(0, 4).map((st) =>
              st.references > 0 ? <img key={st.id} src={styleRefUrl(st, 0)} alt="" /> : <span key={st.id} className="dot2" style={{ background: st.palette.body.hex }} />,
            )}
          </span>
          <span className="stack" style={{ gap: 4 }}>
            <span className="label">{styles.length} style{styles.length === 1 ? "" : "s"}</span>
            <strong style={{ fontWeight: 500 }}>Make your own style →</strong>
          </span>
        </a>
      </div>

      <div className="grid">
        <NewObject styles={styles} claude={!!settings?.claude.has_key && settings.claude.auto_name} />
        {jobs?.map((j) => (
          <a key={j.id} className="card tile" href={`#/job/${j.id}`}>
            <div className="thumb">{j.thumbnail ? <img src={fileUrl(j, j.thumbnail)} alt="" loading="lazy" /> : <span className="muted">No image</span>}</div>
            <div className="meta">
              <strong>{j.name}</strong>
              <span className="small muted">{styleName(j.style.id, j.style_name)}</span>
              <span className={`stage ${j.stage === "exported" ? "done" : ""} ${j.task && (j.task.status === "running" || j.task.status === "queued") ? "busy" : ""}`}>
                {STAGE_LABEL[j.stage]} · {timeAgo(j.updated)}
              </span>
            </div>
          </a>
        ))}
      </div>
      {jobs && jobs.length === 0 && <p className="empty">No objects yet. Start with a photo of a mug, a lamp, a shoe… anything.</p>}
    </div>
  );
}

export function StylePicker({ styles, value, onChange }: { styles: Style[]; value: string; onChange: (id: string) => void }) {
  return (
    <div className="style-pick" role="radiogroup" aria-label="Style">
      {styles.map((s) => (
        <button key={s.id} type="button" role="radio" aria-checked={value === s.id} className={value === s.id ? "on" : ""} onClick={() => onChange(s.id)}>
          {s.references > 0 ? <img src={styleRefUrl(s, 0)} alt="" /> : <span className="dot2" style={{ background: s.palette.body.hex }} />}
          {s.name}
        </button>
      ))}
    </div>
  );
}

const MAKE_AS = [
  { id: "magnet", label: "Magnet", hint: "Fridge magnet: flat, with a pocket for a magnet in the back." },
  { id: "keychain", label: "Keychain", hint: "Flat, with a key-ring loop on top." },
  { id: "ornament", label: "Ornament", hint: "A thin hanging relief with a small loop for a ribbon." },
  { id: "stand", label: "Figurine", hint: "Stands on a round base; its 3D shape is made by an AI 3D model." },
];

function NewObject({ styles, claude }: { styles: Style[]; claude: boolean }) {
  const [styleId, setStyleId] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [fmt, setFmt] = useState("magnet");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState<"auto" | "steps" | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();

  const pick = (f: File | undefined) => {
    if (!f) return;
    if (!/^image\//.test(f.type) && !/\.(heic|webp)$/i.test(f.name)) return toast("That isn't an image.", true);
    setFile(f);
    setPreview(URL.createObjectURL(f));
    // with Claude on, leave it empty so Claude names it; otherwise guess from the file name
    // names like "Pasted 11 11 2026 at 11.57.58", "Screenshot …" or "IMG_1234" say nothing about the object
    const guess = f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
    const meaningless = /^(pasted|screenshot|screen shot|image|img|dsc|photo|pxl|whatsapp)\b/i.test(guess) || !/[a-z]{3}/i.test(guess);
    setSubject(claude || meaningless ? "" : guess);
  };

  const create = async (auto: boolean) => {
    if (!file) return;
    setBusy(auto ? "auto" : "steps");
    try {
      const job = await api.create(file, subject.trim() || (claude ? "" : "object"), styleId || styles[0]?.id || "clay-minimal", fmt);
      if (auto) await api.autoMake(job.id, fmt).catch(() => {}); // the job page shows any problem
      go(`/job/${job.id}`);
    } catch (e) {
      toast((e as Error).message, true);
      setBusy(null);
    }
  };

  if (file && preview) {
    return (
      <div className="card panel stack" style={{ alignContent: "start" }}>
        <img src={preview} alt="" style={{ aspectRatio: "1", objectFit: "contain", background: "#fff", borderRadius: 10, border: "1px solid var(--line)" }} />
        <div className="field">
          <span>What do you want to make?</span>
          <div className="type-grid" role="radiogroup" aria-label="What it becomes">
            {MAKE_AS.map((m) => (
              <button key={m.id} type="button" role="radio" aria-checked={fmt === m.id} className={fmt === m.id ? "on" : ""} onClick={() => setFmt(m.id)}>
                {m.label}
              </button>
            ))}
          </div>
          <span className="small muted">{MAKE_AS.find((m) => m.id === fmt)?.hint}</span>
        </div>
        <label className="field">
          <span>What is the object?</span>
          <input
            className="input"
            autoFocus
            value={subject}
            placeholder={claude ? "Leave empty and Claude will name it" : "e.g. coffee mug"}
            onChange={(e) => setSubject(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create(true)}
          />
        </label>
        {styles.length > 1 && (
          <div className="field">
            <span>Style</span>
            <StylePicker styles={styles} value={styleId || styles[0].id} onChange={setStyleId} />
          </div>
        )}
        <div className="row">
          <button className="btn primary grow" onClick={() => create(true)} disabled={!!busy}>
            {busy === "auto" ? (claude && !subject.trim() ? "Preparing photo, Claude is naming it…" : "Removing background…") : "Make it now"}
          </button>
          <button className="btn ghost" onClick={() => create(false)} disabled={!!busy} title="Choose the style options, the 3D engine and every setting yourself">
            {busy === "steps" ? "Preparing…" : "Step by step"}
          </button>
          <button className="btn ghost" onClick={() => (setFile(null), setPreview(null))} disabled={!!busy}>
            Cancel
          </button>
        </div>
        <span className="small muted">
          Make it now picks everything for you and lands on the finished print. Step by step lets you choose each option along the way.
        </span>
      </div>
    );
  }

  return (
    <div
      className={`drop ${over ? "over" : ""}`}
      role="button"
      tabIndex={0}
      onClick={() => input.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      onDragOver={(e) => (e.preventDefault(), setOver(true))}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        pick(e.dataTransfer.files[0]);
      }}
    >
      <div>
        <div className="plus" aria-hidden="true">+</div>
        <strong style={{ fontWeight: 500 }}>New object</strong>
        <p className="small muted" style={{ margin: "4px 0 0" }}>
          Drop a photo here or click to choose
        </p>
      </div>
      <input ref={input} type="file" accept="image/*,.heic" hidden onChange={(e) => pick(e.target.files?.[0])} />
    </div>
  );
}
