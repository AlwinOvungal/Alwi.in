import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { formatSeconds, type Engine, type Task } from "./api";

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
type Toast = { text: string; bad?: boolean } | null;
const ToastCtx = createContext<(text: string, bad?: boolean) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<Toast>(null);
  const show = useCallback((text: string, bad = false) => setToast({ text, bad }), []);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), toast.bad ? 6000 : 2800);
    return () => clearTimeout(t);
  }, [toast]);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {toast && (
        <div className={`toast ${toast.bad ? "bad" : ""}`} role="status">
          {toast.text}
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
