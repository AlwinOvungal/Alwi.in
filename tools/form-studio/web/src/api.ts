// Typed client for the local Form Studio server.
import { useEffect, useRef, useState } from "react";

export type Stage = "new" | "cutout" | "stylized" | "confirmed" | "reconstructed" | "printified" | "exported";
export const STAGES: Stage[] = ["new", "cutout", "stylized", "confirmed", "reconstructed", "printified", "exported"];

export interface Task {
  kind: string;
  status: "queued" | "running" | "done" | "error";
  progress: number;
  message: string;
  error: string | null;
}

export interface Candidate {
  file: string;
  seed: number | null;
  engine: string;
}

export interface PrintParams {
  geometry: {
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
  reconstruct: { engine: string; seed: number } | null;
  print_params: PrintParams | null;
  report: Report | null;
  exports: string[];
  thumbnail: string | null;
  files: string[];
  task: Task | null;
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
  has_reference: boolean;
}

export interface Settings {
  engines: { stylize: string; reconstruct: string };
  device: string;
  resolved_device: string;
  fast_mode: boolean;
  candidates: number;
  export_formats: string[];
  fal: { stylize_endpoint: string; reconstruct_endpoint: string; has_key: boolean; key_hint: string };
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

export const api = {
  styles: () => req<Style[]>("GET", "/api/styles"),
  engines: () => req<Engines>("GET", "/api/engines"),
  settings: () => req<Settings>("GET", "/api/settings"),
  saveSettings: (s: Partial<Omit<Settings, "fal">> & { fal?: Partial<Settings["fal"]>; fal_key?: string }) =>
    req<Settings>("PUT", "/api/settings", s),
  testKey: () => req<{ ok: boolean; message: string }>("POST", "/api/settings/test-key"),
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
  stylize: (id: string, engine?: string, seed?: number) => req<Job>("POST", `/api/jobs/${id}/stylize`, { engine, seed }),
  confirm: (id: string, candidate: number | null) => req<Job>("POST", `/api/jobs/${id}/confirm`, { candidate }),
  reconstruct: (id: string, engine?: string) => req<Job>("POST", `/api/jobs/${id}/reconstruct`, { engine }),
  printify: (id: string, overrides: Partial<{ [K in keyof PrintParams]: Partial<PrintParams[K]> }>) =>
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
