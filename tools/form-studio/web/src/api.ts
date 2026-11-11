// Typed client for the local Form Studio server.
import { useEffect, useRef, useState } from "react";

export type Stage = "new" | "cutout" | "stylized" | "confirmed" | "reconstructed" | "printified" | "exported";
export const STAGES: Stage[] = ["new", "cutout", "stylized", "confirmed", "reconstructed", "printified", "exported"];

export interface Task {
  /** a new number for every run */
  id?: number;
  kind: string;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  progress: number;
  message: string;
  error: string | null;
  /** the service's own words, for the folded "details" */
  error_detail?: string;
  /** where to fix it, e.g. OpenAI Billing */
  error_links?: { label: string; url: string }[];
  /** what to do, in order */
  error_steps?: string[];
  /** seconds since it started */
  elapsed: number;
  /** expected total seconds, from past runs on this Mac (null until there's history) */
  eta_s: number | null;
  cancelling: boolean;
}

export type Quality = "best" | "quick" | "lightest";
export const QUALITIES: { id: Quality; label: string; hint: string }[] = [
  { id: "best", label: "Best", hint: "30 steps. Most faithful to your style pictures; ~1–2 min per option." },
  { id: "quick", label: "Quick", hint: "SDXL-Lightning, 8 steps. About 3–4× faster; slightly less detail. ~400 MB download once." },
  { id: "lightest", label: "Lightest", hint: "SDXL-Turbo at 512 px. For 8 GB Macs; loosest match to the style." },
];

export interface StepTime {
  seconds: number;
  engine?: string;
  cached?: boolean;
  n?: number;
  first_option?: number | null;
}

export interface Candidate {
  file: string;
  seed: number | null;
  engine: string;
  n?: number;
  style_id?: string;
  style_name?: string;
  quality?: Quality;
}

export type Finish = "soft" | "voxel" | "faceted";
export const FINISHES: { id: Finish; label: string; hint: string }[] = [
  { id: "soft", label: "Soft", hint: "Smooth, rounded clay-like surfaces." },
  { id: "voxel", label: "Pixel / voxel", hint: "Rebuilt from cubes, like 3D pixel art." },
  { id: "faceted", label: "Low-poly", hint: "Flat geometric facets, papercraft feel." },
];

export type PrintFormat = "stand" | "magnet" | "keychain";

export interface PrintParams {
  format: PrintFormat;
  flat_back: { enabled: boolean; thickness_mm: number };
  ring: { enabled: boolean; outer_radius_mm: number; hole_mm: number; thickness_mm: number };
  geometry: {
    finish?: Finish;
    voxel_size_mm?: number;
    facets?: number;
    height_mm: number;
    voxel_mm: number;
    soften_mm: number;
    min_feature_mm: number;
    smooth_iters: number;
    target_faces: number;
    flat_cut: number;
  };
  base: { enabled: boolean; margin_mm: number; thickness_mm: number; fillet_mm: number };
  magnet: { enabled: boolean; diameter_mm: number; depth_mm: number; clearance_mm: number };
}

export interface Report {
  watertight: boolean;
  size_mm: [number, number, number];
  body_volume_cm3: number;
  base_volume_cm3: number;
  faces: number;
  remesh_mm: number;
  min_feature_mm: number;
  thin_removed_pct: number;
  overhang_pct: number;
  warnings: string[];
}

export interface Job {
  id: string;
  name: string;
  subject: string;
  style: { id: string; hash: string };
  stage: Stage;
  created: string;
  updated: string;
  input: string | null;
  candidates: Candidate[];
  confirmed: (Candidate & { index: number | null }) | null;
  reconstruct: { engine: string; seed: number; cached?: boolean } | null;
  print_params: PrintParams | null;
  report: (Report & { format?: PrintFormat }) | null;
  exports: string[];
  thumbnail: string | null;
  files: string[];
  task: Task | null;
  timings?: Partial<Record<"cutout" | "stylize" | "reconstruct" | "printify" | "export", StepTime>>;
}

export interface Engine {
  id: string;
  label: string;
  kind: "local" | "cloud" | "offline";
  note: string;
  available: boolean;
  reason: string;
}

export interface Engines {
  stylize: Engine[];
  reconstruct: Engine[];
  device: string;
}

export interface Style {
  id: string;
  name: string;
  version: number;
  description: string;
  hash: string;
  palette: Record<"body" | "base", { name: string; hex: string }>;
  seeds: number[];
  formats: { id: PrintFormat; label: string }[];
  has_reference: boolean;
  finish: Finish;
  references: number;
  editable: boolean;
}

export interface Colour {
  name: string;
  hex: string;
}

export interface StyleDetail extends Style {
  prompt: string;
  edit_prompt: string;
  negative_prompt: string;
  sculpt_guide: string;
  geometry: StyleGeometry;
  reference_mode: "style" | "full";
}

export interface StyleGeometry {
  height_mm: number;
  soften_mm: number;
  min_feature_mm: number;
  finish: Finish;
  voxel_size_mm: number;
  facets: number;
}

export interface StyleEdit {
  name: string;
  description: string;
  prompt?: string;
  edit_prompt?: string;
  negative_prompt?: string;
  sculpt_guide?: string;
  palette?: { body: Colour; base: Colour };
  geometry?: StyleGeometry;
  based_on?: string;
  reference_mode?: "style" | "full";
}

export interface StyleDraft {
  name: string;
  description: string;
  prompt: string;
  edit_prompt: string;
  negative_prompt: string;
  sculpt_guide: string;
  body: Colour;
  base: Colour;
  soften_mm: number;
  min_feature_mm: number;
  finish: Finish;
}

export const styleRefUrl = (s: Pick<Style, "id" | "version">, n: number) => `/api/styles/${s.id}/references/${n}?v=${s.version}`;

export interface Settings {
  engines: { stylize: string; reconstruct: string };
  device: string;
  resolved_device: string;
  sdxl_quality: Quality;
  keep_loaded: "auto" | "one";
  warm_up: boolean;
  memory: { ram_gb: number | null; budget_gb: number; loaded: string[] };
  candidates: number;
  export_formats: string[];
  fal: { endpoints: Record<string, string>; has_key: boolean; key_hint: string };
  claude: { model: string; effort: string; auto_name: boolean; has_key: boolean; key_hint: string };
  openai: { model: string; quality: string; has_key: boolean; key_hint: string };
  meshy: { ai_model: string; has_key: boolean; key_hint: string };
  slicer: "bambu" | "orca" | "prusa";
  keychain: boolean;
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export interface StorageItem {
  id: string;
  label: string;
  path: string;
  note: string;
  exists: boolean;
  bytes: number;
}

export interface ModelQueue {
  packs: string[];
  step: number;
  total: number;
  current: string | null;
  status: "running" | "done" | "error" | "stopped";
  message: string;
  error: string | null;
}

export interface ModelPack {
  id: string;
  engine: boolean;
  label: string;
  used_by: string;
  status: "missing" | "partial" | "ready" | "unknown";
  total_bytes: number | null;
  done_bytes: number;
  on_disk_bytes: number;
  download: {
    status: "idle" | "downloading" | "done" | "error" | "stopped";
    done_bytes: number;
    total_bytes: number;
    speed: number;
    eta_s: number | null;
    message: string;
    error: string | null;
  } | null;
}

export interface BenchSummary {
  median: number | null;
  p90: number | null;
  n: number;
}
export interface BenchVariant {
  label: string;
  settings: { keep_loaded: string; quality: string; caches: boolean };
  warm: Record<string, BenchSummary>;
  cold: Record<string, number> | null;
  stages: Record<string, BenchSummary>;
  checks_ok: boolean;
}
export interface BenchReport {
  when: string;
  mac: { ram_gb: number; device: string };
  setup: { style: string; restyle: string; shape: string; photos: number; runs: number; options: number };
  variants: Record<string, BenchVariant>;
  skipped: string[];
  sheet?: string | null;
}

/** "48 s", "2 min 05 s" */
export function formatSeconds(s: number | null | undefined) {
  if (s == null) return "—";
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 90) return `${Math.round(s)} s`;
  return `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, "0")} s`;
}

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

export const api = {
  styles: () => req<Style[]>("GET", "/api/styles"),
  storage: (refresh = false) =>
    req<{ home: string; items: StorageItem[]; total_models_bytes: number }>("GET", `/api/storage${refresh ? "?refresh=1" : ""}`),
  openStorage: (id: string) => req<{ ok: boolean }>("POST", "/api/storage/open", { id }),
  models: () => req<{ packs: ModelPack[]; queue: ModelQueue | null }>("GET", "/api/models"),
  startOver: () => req<ModelQueue>("POST", "/api/models/start-over"),
  reinstallLibraries: () => req<{ opened: boolean; command: string }>("POST", "/api/models/reinstall-libraries"),
  downloadModels: (id: string, fresh = false) => req<unknown>("POST", `/api/models/${id}/download${fresh ? "?fresh=1" : ""}`),
  stopModels: (id: string) => req<{ ok: boolean }>("POST", `/api/models/${id}/stop`),
  deleteModels: (id?: string) => req<{ ok: boolean; freed_bytes: number }>("DELETE", id ? `/api/models/${id}` : "/api/models"),
  engines: () => req<Engines>("GET", "/api/engines"),
  settings: () => req<Settings>("GET", "/api/settings"),
  saveSettings: (
    s: Partial<Omit<Settings, "fal" | "claude" | "openai" | "meshy">> & {
      fal?: Partial<Settings["fal"]>;
      claude?: Partial<Settings["claude"]>;
      openai?: Partial<Settings["openai"]>;
      meshy?: Partial<Settings["meshy"]>;
      fal_key?: string;
      anthropic_key?: string;
      openai_key?: string;
      meshy_key?: string;
    },
  ) => req<Settings>("PUT", "/api/settings", s),
  testKey: (service: "fal" | "anthropic" | "openai" | "meshy") => req<{ ok: boolean; message: string }>("POST", `/api/settings/test-key?service=${service}`),
  style: (id: string) => req<StyleDetail>("GET", `/api/styles/${id}`),
  createStyle: (s: StyleEdit) => req<StyleDetail>("POST", "/api/styles", s),
  updateStyle: (id: string, s: StyleEdit) => req<StyleDetail>("PUT", `/api/styles/${id}`, s),
  deleteStyle: (id: string) => req<{ ok: boolean }>("DELETE", `/api/styles/${id}`),
  duplicateStyle: (id: string) => req<StyleDetail>("POST", `/api/styles/${id}/duplicate`),
  addReferences: (id: string, files: File[]) => {
    const f = new FormData();
    files.forEach((file) => f.append("image", file));
    return req<StyleDetail>("POST", `/api/styles/${id}/references`, f);
  },
  makeReferenceFirst: (id: string, n: number) => req<StyleDetail>("POST", `/api/styles/${id}/references/${n}/first`),
  suggestColours: (opts: { files?: File[]; style?: string }) => {
    const f = new FormData();
    (opts.files ?? []).forEach((file) => f.append("image", file));
    return req<{ body: Colour; base: Colour }>("POST", `/api/styles/suggest-colours${opts.style ? `?style=${opts.style}` : ""}`, f);
  },
  removeCandidate: (id: string, index: number) => req<Job>("DELETE", `/api/jobs/${id}/candidates/${index}`),
  clearCandidates: (id: string) => req<Job>("DELETE", `/api/jobs/${id}/candidates`),
  removeReference: (id: string, n: number) => req<StyleDetail>("DELETE", `/api/styles/${id}/references/${n}`),
  draftStyle: (id: string, notes: string) => req<StyleDraft>("POST", `/api/styles/${id}/draft`, { notes }),
  setJobStyle: (id: string, style: string) => req<Job>("POST", `/api/jobs/${id}/style`, { style }),
  suggestName: (id: string) => req<{ subject: string }>("POST", `/api/jobs/${id}/suggest-name`),
  rebuildRecipe: (id: string) => req<Job>("POST", `/api/jobs/${id}/rebuild-recipe`),
  jobs: () => req<Job[]>("GET", "/api/jobs"),
  job: (id: string) => req<Job>("GET", `/api/jobs/${id}`),
  create: (photo: File, subject: string, style: string) => {
    const f = new FormData();
    f.append("photo", photo);
    f.append("subject", subject);
    f.append("style", style);
    return req<Job>("POST", "/api/jobs", f);
  },
  rename: (id: string, patch: { name?: string; subject?: string }) => req<Job>("PATCH", `/api/jobs/${id}`, patch),
  remove: (id: string) => req<{ ok: boolean }>("DELETE", `/api/jobs/${id}`),
  stylize: (id: string, engine?: string, seed?: number, n?: number, quality?: Quality) =>
    req<Job>("POST", `/api/jobs/${id}/stylize`, { engine, seed, n, quality }),
  cancel: (id: string) => req<Job>("POST", `/api/jobs/${id}/cancel`),
  useNow: (id: string, candidate: number, engine?: string) => req<Job>("POST", `/api/jobs/${id}/use-now`, { candidate, engine }),
  bench: () => req<{ task: Task | null; latest: BenchReport | null; photos: number }>("GET", "/api/bench"),
  startBench: (size: "quick" | "full", compare: boolean) =>
    req<{ task: Task | null; latest: BenchReport | null; photos: number }>("POST", "/api/bench", { size, compare }),
  cancelBench: () => req<unknown>("POST", "/api/bench/cancel"),
  confirm: (id: string, candidate: number | null) => req<Job>("POST", `/api/jobs/${id}/confirm`, { candidate }),
  reconstruct: (id: string, engine?: string) => req<Job>("POST", `/api/jobs/${id}/reconstruct`, { engine }),
  printify: (id: string, overrides: Partial<{ [K in keyof PrintParams]: PrintParams[K] extends object ? Partial<PrintParams[K]> : PrintParams[K] }>) =>
    req<Job>("POST", `/api/jobs/${id}/printify`, overrides),
  resetPrint: (id: string) => req<Job>("POST", `/api/jobs/${id}/reset-print`),
  export: (id: string, formats: string[]) => req<Job>("POST", `/api/jobs/${id}/export`, { formats }),
  open: (id: string, app: string, file?: string) => req<{ ok: boolean }>("POST", `/api/jobs/${id}/open`, { app, file }),
};

export function fileUrl(job: Pick<Job, "id" | "updated">, rel: string, download = false) {
  const q = new URLSearchParams({ v: job.updated });
  if (download) q.set("download", "1");
  return `/api/jobs/${job.id}/files/${rel}?${q}`;
}

export function reached(job: Job, stage: Stage) {
  return STAGES.indexOf(job.stage) >= STAGES.indexOf(stage);
}

/** Live job state: initial fetch + Server-Sent Events while the page is open. */
export function useJob(id: string) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let closed = false;
    api.job(id).then(setJob, (e) => setError(String(e.message || e)));
    const es = new EventSource(`/api/jobs/${id}/events`);
    esRef.current = es;
    es.onmessage = (m) => {
      if (closed) return;
      const ev = JSON.parse(m.data);
      if (ev.type === "job") setJob(ev.job);
      else if (ev.type === "task") setJob((j) => (j ? { ...j, task: { ...ev } } : j));
    };
    return () => {
      closed = true;
      es.close();
    };
  }, [id]);

  return { job, setJob, error };
}
