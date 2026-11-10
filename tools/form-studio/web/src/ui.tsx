import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import type { Engine, Task } from "./api";

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
export function Progress({ task }: { task: Task }) {
  const indet = task.status === "queued" || task.progress <= 0.01;
  return (
    <div className="progress" aria-live="polite">
      <div className="row small">
        <span className="grow">{task.message}</span>
        {!indet && <span className="label">{Math.round(task.progress * 100)}%</span>}
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
