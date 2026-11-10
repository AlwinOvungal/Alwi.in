import { useEffect, useState } from "react";
import { api, Engines, Settings } from "./api";
import { EnginePicker, Switch, useToast } from "./ui";

export default function SettingsPage({ onSaved }: { onSaved: (s: Settings) => void }) {
  const [s, setS] = useState<Settings | null>(null);
  const [engines, setEngines] = useState<Engines | null>(null);
  const [key, setKey] = useState("");
  const [keyMsg, setKeyMsg] = useState<{ ok: boolean; message: string } | null>(null);
  const toast = useToast();

  const refresh = () => {
    api.settings().then(setS);
    api.engines().then(setEngines);
  };
  useEffect(refresh, []);

  const save = async (patch: Parameters<typeof api.saveSettings>[0], msg = "Saved") => {
    try {
      const next = await api.saveSettings(patch);
      setS(next);
      onSaved(next);
      api.engines().then(setEngines);
      toast(msg);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  if (!s || !engines) return <div className="page muted">Loading…</div>;
  return (
    <div className="page stack" style={{ gap: 24 }}>
      <div className="stack" style={{ gap: 8 }}>
        <span className="label">Settings</span>
        <h1>How Form Studio works for you</h1>
        <p className="lede">Local engines are free and run on this Mac. Cloud engines are faster and cost a few cents per object, billed by fal.ai.</p>
      </div>

      <div className="settings">
        <div className="card panel stack">
          <h2>Restyle engine</h2>
          <EnginePicker engines={engines.stylize} value={s.engines.stylize} onChange={(id) => save({ engines: { ...s.engines, stylize: id } })} />
          <label className="field">
            <span>Options per run</span>
            <select className="select" value={s.candidates} onChange={(e) => save({ candidates: Number(e.target.value) })}>
              {[1, 2, 3, 4, 6, 8].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <Switch label="Fast mode (SDXL-Turbo, for 8 GB Macs)" checked={s.fast_mode} onChange={(v) => save({ fast_mode: v })} />
        </div>

        <div className="card panel stack">
          <h2>3D engine</h2>
          <EnginePicker engines={engines.reconstruct} value={s.engines.reconstruct} onChange={(id) => save({ engines: { ...s.engines, reconstruct: id } })} />
        </div>

        <div className="card panel stack">
          <h2>fal.ai cloud key</h2>
          <p className="small muted" style={{ margin: 0 }}>
            Optional. Create a key at fal.ai → Dashboard → Keys. It's stored in {s.keychain ? "your macOS Keychain" : "memory until the app quits (no keychain found)"}, never in a file.
          </p>
          {s.fal.has_key ? (
            <div className="row">
              <span className="chip"><span className="dot" />Key saved {s.fal.key_hint}</span>
              <button className="btn sm" onClick={async () => setKeyMsg(await api.testKey())}>Test</button>
              <button className="btn sm ghost danger" onClick={() => (setKeyMsg(null), save({ fal_key: "" }, "Key removed"))}>Remove</button>
            </div>
          ) : (
            <div className="row">
              <input className="input grow" type="password" placeholder="Paste your fal.ai key" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />
              <button className="btn primary" disabled={!key.trim()} onClick={() => (save({ fal_key: key.trim() }, "Key saved"), setKey(""))}>Save</button>
            </div>
          )}
          {keyMsg && <div className={`note ${keyMsg.ok ? "ok" : "bad"}`}>{keyMsg.message}</div>}
          <details>
            <summary className="small muted" style={{ cursor: "pointer" }}>Advanced: model endpoints</summary>
            <div className="stack" style={{ marginTop: 10 }}>
              <label className="field"><span>Restyle endpoint</span><input className="input" placeholder="fal-ai/flux-kontext/dev" defaultValue={s.fal.stylize_endpoint} onBlur={(e) => save({ fal: { stylize_endpoint: e.target.value.trim() } })} /></label>
              <label className="field"><span>3D endpoint</span><input className="input" placeholder="fal-ai/hunyuan3d/v2/mini" defaultValue={s.fal.reconstruct_endpoint} onBlur={(e) => save({ fal: { reconstruct_endpoint: e.target.value.trim() } })} /></label>
            </div>
          </details>
        </div>

        <div className="card panel stack">
          <h2>This Mac</h2>
          <label className="field">
            <span>Run local models on</span>
            <select className="select" value={s.device} onChange={(e) => save({ device: e.target.value })}>
              <option value="auto">Automatic ({s.resolved_device === "mps" ? "Apple GPU" : s.resolved_device})</option>
              <option value="mps">Apple GPU (MPS)</option>
              <option value="cpu">CPU (slow, low memory)</option>
              <option value="cuda">NVIDIA GPU (CUDA)</option>
            </select>
          </label>
          <label className="field">
            <span>Slicer</span>
            <select className="select" value={s.slicer} onChange={(e) => save({ slicer: e.target.value as Settings["slicer"] })}>
              <option value="bambu">Bambu Studio</option>
              <option value="orca">OrcaSlicer</option>
              <option value="prusa">PrusaSlicer</option>
            </select>
          </label>
          <label className="field">
            <span>Default export</span>
            <div className="row small">
              {[["3mf", "3MF"], ["step", "STEP"]].map(([f, label]) => (
                <label key={f} className="row" style={{ gap: 6 }}>
                  <input type="checkbox" checked={s.export_formats.includes(f)} onChange={(e) => save({ export_formats: e.target.checked ? [...s.export_formats, f] : s.export_formats.filter((x) => x !== f) })} />
                  {label}
                </label>
              ))}
            </div>
          </label>
          <p className="small muted" style={{ margin: 0 }}>Your objects live in <span className="kbd">~/FormStudio/jobs</span>.</p>
        </div>
      </div>
    </div>
  );
}
