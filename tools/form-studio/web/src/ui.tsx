import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, formatSeconds, type BlenderStatus, type Engine, type Task } from "./api";

// ---- routing (hash based: works in the app window and any browser) ----
export function useRoute(): string[] {
  const parse = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
export const go = (path: string) => (window.location.hash = path);

// ---- toasts ----
// Short notes ("Saved") come and go. Long errors become a readable card that stays until closed.
type Toast = { text: string; bad?: boolean; id: number } | null;
const ToastCtx = createContext<(text: string, bad?: boolean) => void>(() => {});
const LONG = 90;
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<Toast>(null);
  const show = useCallback((text: string, bad = false) => setToast({ text, bad, id: Date.now() }), []);
  const sticky = !!toast?.bad && toast.text.length > LONG;
  useEffect(() => {
    if (!toast || sticky) return;
    const t = setTimeout(() => setToast(null), toast.bad ? 7000 : 2800);
    return () => clearTimeout(t);
  }, [toast, sticky]);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {toast && (
        <div className={`toast ${toast.bad ? "bad" : ""} ${toast.text.length > LONG ? "long" : ""}`} role={toast.bad ? "alert" : "status"}>
          <span className="grow">{toast.text}</span>
          {(sticky || toast.text.length > LONG) && (
            <button className="toast-close" onClick={() => setToast(null)} aria-label="Close">✕</button>
          )}
        </div>
      )}
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ---- small pieces ----
/** Time left: from the steps done once there's enough progress, else from past runs on this Mac. */
function timeLeft(task: Task, elapsed: number): number | null {
  const p = task.progress;
  if (p > 0.12 && elapsed > 3) return Math.max(0, (elapsed * (1 - p)) / p);
  if (task.eta_s) return Math.max(0, task.eta_s - elapsed);
  return null;
}

function roughly(s: number) {
  if (s < 10) return "a few seconds left";
  if (s < 60) return `about ${Math.round(s / 5) * 5} s left`;
  const m = Math.round(s / 60);
  return `about ${m} min left`;
}

export function Progress({ task, onCancel, cancelLabel = "Cancel" }: { task: Task; onCancel?: () => void; cancelLabel?: string }) {
  const indet = task.status === "queued" || task.progress <= 0.01;
  // the server sends elapsed with each update; keep the clock ticking in between
  const [base, setBase] = useState({ elapsed: task.elapsed ?? 0, at: Date.now() });
  const [, tick] = useState(0);
  useEffect(() => setBase({ elapsed: task.elapsed ?? 0, at: Date.now() }), [task.elapsed, task.status]);
  const running = task.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [running]);
  const elapsed = running ? base.elapsed + (Date.now() - base.at) / 1000 : base.elapsed;
  const left = running ? timeLeft(task, elapsed) : null;
  return (
    <div className="progress" aria-live="polite">
      <div className="row small">
        <span className="grow">{task.cancelling ? "Stopping after this step…" : task.message}</span>
        {running && (
          <span className="label" title="Time so far">
            {formatSeconds(elapsed)}
            {left != null ? ` · ${roughly(left)}` : ""}
          </span>
        )}
        {!indet && <span className="label">{Math.round(task.progress * 100)}%</span>}
        {onCancel && (task.status === "running" || task.status === "queued") && (
          <button className="btn sm ghost" onClick={onCancel} disabled={task.cancelling}>
            {task.cancelling ? "Stopping…" : cancelLabel}
          </button>
        )}
      </div>
      <div className={`bar ${indet ? "indet" : ""}`}>
        <i style={{ width: `${task.progress * 100}%` }} />
      </div>
    </div>
  );
}

export function KindChip({ kind }: { kind: Engine["kind"] }) {
  const text = kind === "local" ? "Free · on this Mac" : kind === "cloud" ? "Cloud · paid" : "Offline · instant";
  return (
    <span className={`chip ${kind}`}>
      <span className="dot" />
      {text}
    </span>
  );
}

export function EnginePicker({
  engines,
  value,
  onChange,
}: {
  engines: Engine[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="stack" role="radiogroup">
      {engines.map((e) => (
        <label
          key={e.id}
          className={`engine-opt ${value === e.id ? "on" : ""} ${e.available ? "" : "off"}`}
          title={e.available ? "" : e.reason}
        >
          <input
            type="radio"
            name="engine"
            checked={value === e.id}
            disabled={!e.available}
            onChange={() => onChange(e.id)}
          />
          <span className="h">
            {e.label}
            <KindChip kind={e.kind} />
          </span>
          <span className="small muted">{e.available ? e.note : e.reason}</span>
        </label>
      ))}
    </div>
  );
}

export function Slider(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit?: string;
  onChange: (v: number) => void;
  onCommit: (v: number) => void;
  disabled?: boolean;
}) {
  const { label, value, min, max, step, unit = "", onChange, onCommit, disabled } = props;
  const decimals = step < 1 ? (step < 0.1 ? 2 : 1) : 0;
  return (
    <label className="slider">
      <span className="top">
        <span>{label}</span>
        <span>
          {value.toFixed(decimals)}
          {unit}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        onPointerUp={(e) => onCommit(Number((e.target as HTMLInputElement).value))}
        onKeyUp={(e) => onCommit(Number((e.target as HTMLInputElement).value))}
      />
    </label>
  );
}

export function Switch({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="switch">
      <span className="small" style={{ fontWeight: 500, color: "var(--ink-2)" }}>
        {label}
      </span>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

export function timeAgo(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

/** A failed step: what happened in plain words, what to do, where to fix it, and the service's own message folded away. */
export function ErrorCard({
  title,
  task,
  onRetry,
  onOther,
  otherLabel,
  onDismiss,
}: {
  title: string;
  task: Task;
  onRetry?: () => void;
  onOther?: () => void;
  otherLabel?: string;
  onDismiss?: () => void;
}) {
  return (
    <div className="error-card" role="alert">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap", alignItems: "start" }}>
        <strong>{title}</strong>
        {onDismiss && (
          <button className="btn sm ghost" onClick={onDismiss} aria-label="Dismiss">
            ✕
          </button>
        )}
      </div>
      <p>{task.error}</p>
      {task.error_steps && task.error_steps.length > 0 && (
        <ol>
          {task.error_steps.map((s) => <li key={s}>{s}</li>)}
        </ol>
      )}
      <div className="row" style={{ gap: 8 }}>
        {task.error_links?.map((l) => (
          <a key={l.url} className="btn sm" href={l.url} target="_blank" rel="noreferrer">
            {l.label} ↗
          </a>
        ))}
        {onOther && <button className="btn sm olive" onClick={onOther}>{otherLabel ?? "Use another engine"}</button>}
        {onRetry && <button className="btn sm" onClick={onRetry}>Try again</button>}
      </div>
      {task.error_detail && (
        <details>
          <summary>What the service said</summary>
          <p className="detail">{task.error_detail}</p>
        </details>
      )}
    </div>
  );
}

/** Whether Blender is on this Mac (for the "Blender clay" finish). Asked once per page. */
export function useBlender(): BlenderStatus | null {
  const [b, setB] = useState<BlenderStatus | null>(null);
  useEffect(() => {
    api.blender().then(setB).catch(() => setB({ found: false, download: "https://www.blender.org/download/" }));
  }, []);
  return b;
}

/** Sliders for the Blender clay finish, shared by the Print step and the style editor. */
export function ClayFields({ g, set, disabled }: {
  g: { clay_texture_mm?: number; clay_grain_mm?: number; clay_smooth?: number };
  set: (patch: { clay_texture_mm?: number; clay_grain_mm?: number; clay_smooth?: number }, commit: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <>
      <Slider label="Clay texture" unit=" mm" min={0} max={1} step={0.05} value={g.clay_texture_mm ?? 0.25} disabled={disabled}
        onChange={(v) => set({ clay_texture_mm: v }, false)} onCommit={(v) => set({ clay_texture_mm: v }, true)} />
      <Slider label="Texture size" unit=" mm" min={1} max={10} step={0.5} value={g.clay_grain_mm ?? 3} disabled={disabled}
        onChange={(v) => set({ clay_grain_mm: v }, false)} onCommit={(v) => set({ clay_grain_mm: v }, true)} />
      <Slider label="Smoothness" unit="" min={0} max={30} step={1} value={g.clay_smooth ?? 10} disabled={disabled}
        onChange={(v) => set({ clay_smooth: v }, false)} onCommit={(v) => set({ clay_smooth: v }, true)} />
    </>
  );
}

export function NeedsBlender({ b }: { b: BlenderStatus | null }) {
  if (!b || b.found) return null;
  return (
    <div className="note info" style={{ display: "block" }}>
      <b>Blender clay needs Blender</b> (free). Until it's there, the Soft finish is used.{" "}
      {b.can_install
        ? <>Install it inside Form Studio with one click: <a href="#/settings">Settings → Printing → Install Blender</a>. No Blender app needed.</>
        : <><a href={b.download} target="_blank" rel="noreferrer">Download Blender</a>, put it in Applications, then press <b>Look again</b> in Settings → Printing.</>}
    </div>
  );
}
