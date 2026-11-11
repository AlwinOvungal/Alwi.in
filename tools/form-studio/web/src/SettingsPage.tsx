import { useEffect, useState } from "react";
import { api, Engines, formatBytes, Settings, StorageItem } from "./api";
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
  service: "fal" | "anthropic" | "openai" | "meshy";
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
  const field = `${service}_key` as "fal_key" | "anthropic_key" | "openai_key" | "meshy_key";
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

function StorageCard() {
  const [data, setData] = useState<{ home: string; items: StorageItem[]; total_models_bytes: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const load = async (refresh = false) => {
    setBusy(true);
    try {
      setData(await api.storage(refresh));
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const home = data?.home ?? "";
  const tidy = (p: string) => (home && (p === home || p.startsWith(home + "/")) ? "~" + p.slice(home.length) : p);

  return (
    <div className="card panel stack storage">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>Storage</h2>
        <button className="btn sm" disabled={busy} onClick={() => load(true)}>{busy ? "Measuring…" : "Refresh"}</button>
      </div>
      {data ? (
        <>
          <p className="small muted" style={{ margin: 0 }}>
            AI models use <b>{formatBytes(data.total_models_bytes)}</b>. If you only use cloud engines, you can delete the models
            folder; models download again when a local engine needs them.
          </p>
          <div className="files">
            {data.items.map((i) => (
              <div key={i.id} className="storage-row">
                <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                  <strong style={{ fontWeight: 500 }}>{i.label}</strong>
                  <span className="kbd path" title={i.path}>{tidy(i.path)}</span>
                  <span className="small muted">{i.note}</span>
                </div>
                <span className="size">{i.exists ? formatBytes(i.bytes) : "Not created yet"}</span>
                <button className="btn sm" disabled={!i.exists} onClick={() => api.openStorage(i.id).catch((e) => toast(e.message, true))}>
                  Show in Finder
                </button>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="small muted" style={{ margin: 0 }}>Measuring folders…</p>
      )}
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
          title="OpenAI key (ChatGPT images)"
          service="openai"
          hasKey={s.openai.has_key}
          hint={s.openai.key_hint}
          keychain={s.keychain}
          where="Powers the ChatGPT images restyle engine. Create one at platform.openai.com → API keys (billing must be set up)."
          placeholder="Paste your OpenAI API key"
          save={save}
        >
          <label className="field">
            <span>Image model</span>
            <select className="select" value={s.openai.model} onChange={(e) => save({ openai: { model: e.target.value } })}>
              <option value="gpt-image-2.5-sunburst">GPT Image 2.5 Sunburst (most precise)</option>
              <option value="gpt-image-2.5-flare">GPT Image 2.5 Flare</option>
              <option value="gpt-image-2">GPT Image 2</option>
            </select>
          </label>
          <label className="field">
            <span>Quality (higher costs more)</span>
            <select className="select" value={s.openai.quality} onChange={(e) => save({ openai: { quality: e.target.value } })}>
              <option value="low">Low (cheapest, good for trying ideas)</option>
              <option value="medium">Medium (recommended)</option>
              <option value="high">High</option>
            </select>
          </label>
        </KeyCard>

        <KeyCard
          title="Meshy key (3D)"
          service="meshy"
          hasKey={s.meshy.has_key}
          hint={s.meshy.key_hint}
          keychain={s.keychain}
          where="Powers the Meshy image-to-3D engine. API access needs a Meshy Pro plan or higher; create a key at meshy.ai → Settings → API."
          placeholder="Paste your Meshy API key"
          save={save}
        >
          <label className="field">
            <span>Meshy model</span>
            <select className="select" value={s.meshy.ai_model} onChange={(e) => save({ meshy: { ai_model: e.target.value } })}>
              <option value="latest">Meshy 7.1 (best shapes, ~20 credits)</option>
              <option value="meshy-6-lite">Meshy 6 Lite (cheaper, ~5 credits)</option>
            </select>
          </label>
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
      <StorageCard />
    </div>
  );
}
