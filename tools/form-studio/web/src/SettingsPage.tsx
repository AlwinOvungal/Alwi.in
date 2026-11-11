import { useEffect, useState } from "react";
import { api, Engines, Settings } from "./api";
import { EnginePicker, Switch, useToast } from "./ui";

type Save = (patch: Parameters<typeof api.saveSettings>[0], msg?: string) => Promise<void>;

const FAL_ENGINES: [string, string, string][] = [
  ["fal-nano-banana", "Nano Banana (restyle)", "fal-ai/nano-banana/edit"],
  ["fal-kontext", "FLUX Kontext (restyle)", "fal-ai/flux-kontext/dev"],
  ["fal-trellis", "TRELLIS (3D)", "fal-ai/trellis"],
  ["fal-hunyuan", "Hunyuan3D mini (3D)", "fal-ai/hunyuan3d/v2/mini"],
];

function KeyCard(props: {
  title: string;
  service: "fal" | "anthropic";
  hasKey: boolean;
  hint: string;
  keychain: boolean;
  where: string;
  placeholder: string;
  save: Save;
  children?: React.ReactNode;
}) {
  const { title, service, hasKey, hint, keychain, where, placeholder, save, children } = props;
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; message: string } | null>(null);
  const field = service === "fal" ? "fal_key" : "anthropic_key";
  return (
    <div className="card panel stack">
      <h2>{title}</h2>
      <p className="small muted" style={{ margin: 0 }}>
        Optional. {where} Stored in {keychain ? "your macOS Keychain" : "memory until the app quits (no keychain found)"}, never in a file.
      </p>
      {hasKey ? (
        <div className="row">
          <span className="chip">
            <span className="dot" />
            Key saved {hint}
          </span>
          <button className="btn sm" onClick={async () => setMsg(await api.testKey(service))}>Test</button>
          <button className="btn sm ghost danger" onClick={() => (setMsg(null), save({ [field]: "" }, "Key removed"))}>Remove</button>
        </div>
      ) : (
        <div className="row">
          <input className="input grow" type="password" placeholder={placeholder} value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />
          <button className="btn primary" disabled={!key.trim()} onClick={() => (save({ [field]: key.trim() }, "Key saved"), setKey(""))}>Save</button>
        </div>
      )}
      {msg && <div className={`note ${msg.ok ? "ok" : "bad"}`}>{msg.message}</div>}
      {children}
    </div>
  );
}

export default function SettingsPage({ onSaved }: { onSaved: (s: Settings) => void }) {
  const [s, setS] = useState<Settings | null>(null);
  const [engines, setEngines] = useState<Engines | null>(null);
  const toast = useToast();

  useEffect(() => {
    api.settings().then(setS);
    api.engines().then(setEngines);
  }, []);

  const save: Save = async (patch, msg = "Saved") => {
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
        <p className="lede">
          Local engines are free and run on this Mac. Cloud engines are faster and cost a few cents per object, billed by fal.ai or Anthropic.
          You can also switch engines for each object as you go.
        </p>
      </div>

      <div className="settings">
        <div className="card panel stack">
          <h2>Default restyle engine</h2>
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
          <h2>Default 3D engine</h2>
          <EnginePicker engines={engines.reconstruct} value={s.engines.reconstruct} onChange={(id) => save({ engines: { ...s.engines, reconstruct: id } })} />
        </div>

        <KeyCard
          title="Anthropic key (Claude)"
          service="anthropic"
          hasKey={s.claude.has_key}
          hint={s.claude.key_hint}
          keychain={s.keychain}
          where="Powers Claude Sculpt, naming objects and drafting styles. Create one at console.anthropic.com → API Keys."
          placeholder="Paste your Anthropic API key"
          save={save}
        >
          <label className="field">
            <span>Claude model</span>
            <select className="select" value={s.claude.model} onChange={(e) => save({ claude: { model: e.target.value } })}>
              <option value="claude-opus-5-5">Claude Opus 5.5 (best results)</option>
              <option value="claude-sonnet-5-5">Claude Sonnet 5.5 (cheaper)</option>
            </select>
          </label>
          <label className="field">
            <span>How hard Claude thinks when sculpting</span>
            <select className="select" value={s.claude.effort} onChange={(e) => save({ claude: { effort: e.target.value } })}>
              <option value="low">Low (fastest, cheapest)</option>
              <option value="medium">Medium (recommended)</option>
              <option value="high">High (most careful)</option>
            </select>
          </label>
          <Switch label="Name new objects automatically" checked={s.claude.auto_name} onChange={(v) => save({ claude: { auto_name: v } })} />
        </KeyCard>

        <KeyCard
          title="fal.ai key (cloud images & 3D)"
          service="fal"
          hasKey={s.fal.has_key}
          hint={s.fal.key_hint}
          keychain={s.keychain}
          where="Powers Nano Banana, FLUX Kontext, TRELLIS and Hunyuan3D in the cloud. Create one at fal.ai → Dashboard → Keys."
          placeholder="Paste your fal.ai key"
          save={save}
        >
          <details>
            <summary className="small muted" style={{ cursor: "pointer" }}>Advanced: model endpoints</summary>
            <div className="stack" style={{ marginTop: 10 }}>
              {FAL_ENGINES.map(([id, label, placeholder]) => (
                <label key={id} className="field">
                  <span>{label}</span>
                  <input
                    className="input"
                    placeholder={placeholder}
                    defaultValue={s.fal.endpoints?.[id] ?? ""}
                    onBlur={(e) => save({ fal: { endpoints: { ...s.fal.endpoints, [id]: e.target.value.trim() } } })}
                  />
                </label>
              ))}
            </div>
          </details>
        </KeyCard>

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
          <p className="small muted" style={{ margin: 0 }}>Your objects live in <span className="kbd">~/FormStudio/jobs</span>, your styles in <span className="kbd">~/FormStudio/styles</span>.</p>
        </div>
      </div>
    </div>
  );
}
