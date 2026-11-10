import { useEffect, useRef, useState } from "react";
import { api, fileUrl, Job, Style } from "./api";
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
  const toast = useToast();

  useEffect(() => {
    api.jobs().then(setJobs, (e) => toast(e.message, true));
    api.styles().then(setStyles).catch(() => {});
  }, [toast]);

  return (
    <div className="page">
      <div className="lib-head">
        <div className="stack" style={{ gap: 8 }}>
          <span className="label">Photo → house style → print</span>
          <h1>Your objects</h1>
          <p className="lede">
            Drop in a photo of anything. Form Studio restyles it in your house style, builds a 3D shape and
            prepares a two-colour print file.
          </p>
        </div>
        {styles[0] && (
          <div className="card panel ref" style={{ maxWidth: 360 }}>
            <img src={`/api/styles/${styles[0].id}/reference`} alt="Style reference" />
            <div className="stack" style={{ gap: 4 }}>
              <span className="label">Style · v{styles[0].version}</span>
              <strong style={{ fontWeight: 500 }}>{styles[0].name}</strong>
              <div className="swatches">
                {Object.values(styles[0].palette).map((c) => (
                  <span key={c.hex} className="swatch" style={{ background: c.hex }} title={c.name} />
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="grid">
        <NewObject styleId={styles[0]?.id ?? "clay-minimal"} />
        {jobs?.map((j) => (
          <a key={j.id} className="card tile" href={`#/job/${j.id}`}>
            <div className="thumb">{j.thumbnail ? <img src={fileUrl(j, j.thumbnail)} alt="" loading="lazy" /> : <span className="muted">No image</span>}</div>
            <div className="meta">
              <strong>{j.name}</strong>
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

function NewObject({ styleId }: { styleId: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();

  const pick = (f: File | undefined) => {
    if (!f) return;
    if (!/^image\//.test(f.type) && !/\.(heic|webp)$/i.test(f.name)) return toast("That isn't an image.", true);
    setFile(f);
    setPreview(URL.createObjectURL(f));
    setSubject(f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").replace(/\b(img|dsc|photo)\s*\d*\b/gi, "").trim());
  };

  const create = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const job = await api.create(file, subject || "object", styleId);
      go(`/job/${job.id}`);
    } catch (e) {
      toast((e as Error).message, true);
      setBusy(false);
    }
  };

  if (file && preview) {
    return (
      <div className="card panel stack" style={{ alignContent: "start" }}>
        <img src={preview} alt="" style={{ aspectRatio: "1", objectFit: "contain", background: "#fff", borderRadius: 10, border: "1px solid var(--line)" }} />
        <label className="field">
          <span>What is it?</span>
          <input
            className="input"
            autoFocus
            value={subject}
            placeholder="e.g. coffee mug"
            onChange={(e) => setSubject(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
          />
        </label>
        <div className="row">
          <button className="btn primary grow" onClick={create} disabled={busy}>
            {busy ? "Removing background…" : "Start"}
          </button>
          <button className="btn ghost" onClick={() => (setFile(null), setPreview(null))} disabled={busy}>
            Cancel
          </button>
        </div>
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
