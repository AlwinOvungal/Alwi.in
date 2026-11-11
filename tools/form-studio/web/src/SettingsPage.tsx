import { ReactNode, useEffect, useRef, useState } from "react";
import { api, Engine, Engines, formatBytes, ModelPack, ModelQueue, Settings, StorageItem } from "./api";
import { KindChip, Switch, useToast } from "./ui";

type Save = (patch: Parameters<typeof api.saveSettings>[0], msg?: string) => Promise<void>;
type Service = "anthropic" | "openai" | "meshy" | "fal";

const SECTIONS = [
  ["engines", "Engines"],
  ["accounts", "Cloud accounts"],
  ["local", "Local AI"],
  ["printing", "Printing"],
  ["storage", "Storage"],
] as const;
type SectionId = (typeof SECTIONS)[number][0];

const FAL_ENGINES: [string, string, string][] = [
  ["fal-nano-banana", "Nano Banana (restyle)", "fal-ai/nano-banana/edit"],
  ["fal-kontext", "FLUX Kontext (restyle)", "fal-ai/flux-kontext/dev"],
  ["fal-trellis", "TRELLIS (3D)", "fal-ai/trellis"],
  ["fal-hunyuan", "Hunyuan3D mini (3D)", "fal-ai/hunyuan3d/v2/mini"],
];

/** Which account or section unlocks an engine that isn't available yet. */
function unlockFor(e: Engine): { service?: Service; section: SectionId; label: string } {
  if (e.id.startsWith("fal-")) return { service: "fal", section: "accounts", label: "Connect fal.ai" };
  if (e.id === "openai-image") return { service: "openai", section: "accounts", label: "Connect OpenAI" };
  if (e.id === "meshy") return { service: "meshy", section: "accounts", label: "Connect Meshy" };
  if (e.id === "claude-sculpt") return { service: "anthropic", section: "accounts", label: "Connect Anthropic" };
  return { section: "local", label: "Set up" };
}

// ---------- building blocks ----------

function Section({ id, title, intro, action, children }: { id: SectionId; title: string; intro?: ReactNode; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="set-section" id={`set-${id}`} data-section={id}>
      <header className="set-head">
        <div className="stack" style={{ gap: 6, minWidth: 0 }}>
          <h2>{title}</h2>
          {intro && <p className="small muted">{intro}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}

function Group({ title, note, children }: { title?: string; note?: ReactNode; children: ReactNode }) {
  return (
    <div className="set-group">
      {(title || note) && (
        <div className="set-group-title">
          {title && <span className="label">{title}</span>}
          {note && <span className="small muted">{note}</span>}
        </div>
      )}
      <div className="set-list">{children}</div>
    </div>
  );
}

function Row({ title, desc, children, wide }: { title: ReactNode; desc?: ReactNode; children?: ReactNode; wide?: boolean }) {
  return (
    <div className={`set-row ${wide ? "wide" : ""}`}>
      <div className="set-text">
        <span className="t">{title}</span>
        {desc && <span className="d">{desc}</span>}
      </div>
      {children && <div className="set-ctrl">{children}</div>}
    </div>
  );
}

// ---------- engines ----------

function EngineList({ engines, value, onChange, onUnlock }: { engines: Engine[]; value: string; onChange: (id: string) => void; onUnlock: (e: Engine) => void }) {
  const ready = engines.filter((e) => e.available);
  const locked = engines.filter((e) => !e.available);
  const [showLocked, setShowLocked] = useState(false);
  return (
    <>
      <div role="radiogroup">
        {ready.map((e) => (
          <label key={e.id} className={`set-row engine-row ${value === e.id ? "on" : ""}`}>
            <input type="radio" checked={value === e.id} onChange={() => onChange(e.id)} />
            <span className="radio" aria-hidden />
            <span className="set-text">
              <span className="t">{e.label}</span>
              <span className="d">{e.note}</span>
            </span>
            <span className="set-ctrl"><KindChip kind={e.kind} /></span>
          </label>
        ))}
      </div>
      {locked.length > 0 && (
        <>
          <button className="set-row set-more" onClick={() => setShowLocked((v) => !v)} aria-expanded={showLocked}>
            <span className="set-text">
              <span className="t">{locked.length} more need setting up</span>
              <span className="d">{locked.map((e) => e.label.replace(/\s*\(.*\)$/, "")).join(" · ")}</span>
            </span>
            <span className={`chev ${showLocked ? "open" : ""}`} aria-hidden>›</span>
          </button>
          {showLocked &&
            locked.map((e) => (
              <div key={e.id} className="set-row engine-row locked">
                <span className="radio" aria-hidden />
                <span className="set-text">
                  <span className="t">{e.label}</span>
                  <span className="d">{e.reason.replace(/\s+in Settings(?=[.\s(]|$)/, "")}</span>
                </span>
                <span className="set-ctrl">
                  <button className="btn sm" onClick={() => onUnlock(e)}>{unlockFor(e).label}</button>
                </span>
              </div>
            ))}
        </>
      )}
    </>
  );
}

// ---------- accounts ----------

function Account(props: {
  service: Service;
  name: string;
  powers: string;
  where: string;
  placeholder: string;
  hasKey: boolean;
  hint: string;
  open: boolean;
  setOpen: (v: boolean) => void;
  save: Save;
  children?: ReactNode;
}) {
  const { service, name, powers, where, placeholder, hasKey, hint, open, setOpen, save, children } = props;
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const field = `${service}_key` as "fal_key" | "anthropic_key" | "openai_key" | "meshy_key";
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (open && !hasKey) ref.current?.focus();
  }, [open, hasKey]);
  return (
    <div className={`account ${open ? "open" : ""}`} id={`acct-${service}`}>
      <button className="set-row account-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="set-text">
          <span className="t">{name}</span>
          <span className="d">{powers}</span>
        </span>
        <span className="set-ctrl">
          {hasKey ? (
            <span className="chip"><span className="dot" />Connected</span>
          ) : (
            <span className="chip offline"><span className="dot" />Not connected</span>
          )}
          <span className={`chev ${open ? "open" : ""}`} aria-hidden>›</span>
        </span>
      </button>
      {open && (
        <div className="account-body stack">
          {hasKey ? (
            <div className="row">
              <span className="small muted grow">Key <span className="kbd">{hint}</span> is saved.</span>
              <button
                className="btn sm"
                disabled={testing}
                onClick={async () => {
                  setTesting(true);
                  try {
                    setMsg(await api.testKey(service));
                  } finally {
                    setTesting(false);
                  }
                }}
              >
                {testing ? "Testing…" : "Test key"}
              </button>
              <button className="btn sm ghost danger" onClick={() => confirm(`Remove your ${name} key?`) && (setMsg(null), save({ [field]: "" }, "Key removed"))}>
                Remove
              </button>
            </div>
          ) : (
            <form
              className="row"
              onSubmit={(e) => {
                e.preventDefault();
                if (key.trim()) save({ [field]: key.trim() }, `${name} connected`).then(() => setKey(""));
              }}
            >
              <input ref={ref} className="input grow" type="password" placeholder={placeholder} value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />
              <button className="btn primary" disabled={!key.trim()}>Save key</button>
            </form>
          )}
          {!hasKey && <span className="small muted">{where}</span>}
          {msg && <div className={`note ${msg.ok ? "ok" : "bad"}`}>{msg.message}</div>}
          {children && <div className="account-opts">{children}</div>}
        </div>
      )}
    </div>
  );
}

// ---------- local AI ----------

function LocalModels({ s, save }: { s: Settings; save: Save }) {
  const [packs, setPacks] = useState<ModelPack[] | null>(null);
  const [queue, setQueue] = useState<ModelQueue | null>(null);
  const [reinstall, setReinstall] = useState<{ opened: boolean; command: string } | null>(null);
  const toast = useToast();
  const load = () => api.models().then((r) => (setPacks(r.packs), setQueue(r.queue))).catch((e) => toast(e.message, true));
  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const downloading = packs?.some((p) => p.download?.status === "downloading") || queue?.status === "running";
  useEffect(() => {
    if (!downloading) return;
    const t = setInterval(load, 1000);
    return () => clearInterval(t);
  }, [downloading]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (fn: () => Promise<unknown>, msg?: string) => {
    try {
      await fn();
      if (msg) toast(msg);
    } catch (e) {
      toast((e as Error).message, true);
    }
    load();
  };
  const onDisk = packs?.reduce((n, p) => n + p.on_disk_bytes, 0) ?? 0;
  const readyCount = packs?.filter((p) => p.status === "ready").length ?? 0;
  const label = (id: string) => packs?.find((p) => p.id === id)?.label ?? id;
  const startOver = () => {
    if (!confirm(`Start over?\n\nThis deletes every downloaded model and engine (${formatBytes(onDisk)}) and then downloads fresh copies of what your engines need.`)) return;
    act(() => api.startOver(), "Starting over: deleted, now downloading fresh copies");
  };
  const reinstallLibs = async () => {
    if (!confirm("Reinstall the AI libraries?\n\nForm Studio will quit and a Terminal window will reinstall PyTorch, diffusers and the other AI libraries. Reopen Form Studio when it finishes.")) return;
    try {
      setReinstall(await api.reinstallLibraries());
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  return (
    <>
      <Group>
        <Row title="Run local models on" desc="Apple GPU is fastest. CPU is slow but uses less memory.">
          <select className="select" value={s.device} onChange={(e) => save({ device: e.target.value })}>
            <option value="auto">Automatic ({s.resolved_device === "mps" ? "Apple GPU" : s.resolved_device})</option>
            <option value="mps">Apple GPU (MPS)</option>
            <option value="cpu">CPU (slow, low memory)</option>
            <option value="cuda">NVIDIA GPU (CUDA)</option>
          </select>
        </Row>
        <Row title="Fast mode" desc="Uses SDXL-Turbo for restyling: quicker and lighter, for 8 GB Macs. Slightly less faithful to the style.">
          <Switch label="" checked={s.fast_mode} onChange={(v) => save({ fast_mode: v })} />
        </Row>
      </Group>

      <Group
        title="Models and engines"
        note={packs ? `${readyCount} of ${packs.length} ready · ${formatBytes(onDisk)} on disk` : "Checking…"}
      >
        {queue && queue.status === "running" && (
          <div className="set-row wide queue">
            <div className="set-text">
              <span className="t">Starting over · step {queue.step} of {queue.total}</span>
              <span className="d">{queue.packs.map((p, i) => `${i < queue.step - 1 ? "✓" : i === queue.step - 1 ? "↓" : "·"} ${label(p)}`).join("   ")}</span>
            </div>
          </div>
        )}
        {queue && queue.status === "done" && <div className="set-row wide"><div className="note ok">{queue.message}</div></div>}
        {queue && (queue.status === "error" || queue.status === "stopped") && (
          <div className="set-row wide">
            <div className="note bad">Start over stopped at {queue.current ? label(queue.current) : "a download"}: {queue.error ?? "stopped"}. Press that row's Download to continue.</div>
          </div>
        )}
        {packs?.map((p) => {
          const d = p.download;
          const active = d?.status === "downloading";
          const total = p.total_bytes ?? d?.total_bytes ?? 0;
          const status = active
            ? "Downloading"
            : p.status === "ready"
              ? "Ready"
              : p.status === "partial"
                ? `${formatBytes(p.done_bytes)} of ${formatBytes(total)}`
                : p.status === "unknown"
                  ? `${formatBytes(p.on_disk_bytes)} on disk`
                  : "Not downloaded";
          return (
            <div key={p.id} className="set-row model">
              <div className="set-text">
                <span className="t">
                  {p.label}
                  {total > 0 && <span className="size">{formatBytes(total)}</span>}
                </span>
                <span className="d">{p.used_by}</span>
                {active && d && (
                  <div className="progress" style={{ marginTop: 6 }}>
                    <span className="small">{d.message}</span>
                    <div className={`bar ${d.done_bytes <= 0 ? "indet" : ""}`}>
                      <i style={{ width: `${d.total_bytes ? (100 * d.done_bytes) / d.total_bytes : 0}%` }} />
                    </div>
                  </div>
                )}
                {!active && d && (d.status === "error" || d.status === "stopped") && (
                  <div className={`note ${d.status === "error" ? "bad" : "info"}`} style={{ marginTop: 6 }}>{d.error || d.message}</div>
                )}
              </div>
              <div className="set-ctrl">
                <span className={`chip ${p.status === "ready" && !active ? "" : "offline"}`}><span className="dot" />{status}</span>
                {active ? (
                  <button className="btn sm" onClick={() => act(() => api.stopModels(p.id))}>Stop</button>
                ) : p.status !== "ready" ? (
                  <button className="btn sm primary" onClick={() => act(() => api.downloadModels(p.id))}>{p.status === "partial" ? "Resume" : "Download"}</button>
                ) : null}
                {!active && p.on_disk_bytes > 0 && (
                  <details className="menu">
                    <summary className="btn sm ghost" aria-label={`More for ${p.label}`}>•••</summary>
                    <div className="menu-pop">
                      <button onClick={(e) => ((e.currentTarget.closest("details") as HTMLDetailsElement).open = false, confirm(`Delete ${p.label} and download it again from scratch?`) && act(() => api.downloadModels(p.id, true)))}>
                        Download fresh
                      </button>
                      <button className="danger" onClick={(e) => ((e.currentTarget.closest("details") as HTMLDetailsElement).open = false, confirm(`Delete ${p.label} (${formatBytes(p.on_disk_bytes)})?`) && act(() => api.deleteModels(p.id), "Deleted"))}>
                        Delete ({formatBytes(p.on_disk_bytes)})
                      </button>
                    </div>
                  </details>
                )}
              </div>
            </div>
          );
        }) ?? <Row title="Checking models…" />}
      </Group>

      <details className="set-group trouble">
        <summary className="set-group-title">
          <span className="label">Troubleshooting</span>
          <span className="small muted">If downloads or local engines misbehave</span>
        </summary>
        <div className="set-list">
          <Row title="Start over" desc={`Deletes every model and engine download (${formatBytes(onDisk)}), then downloads fresh copies of what your engines need, one after another.`}>
            <button className="btn sm" disabled={downloading} onClick={startOver}>Start over…</button>
          </Row>
          <Row title="Delete all downloads" desc="Frees the space. Nothing downloads again until a local engine needs it or you press Download.">
            <button
              className="btn sm ghost danger"
              disabled={!onDisk || downloading}
              onClick={() => confirm(`Delete every downloaded model and engine (${formatBytes(onDisk)})? Nothing is downloaded again until you ask.`) && act(() => api.deleteModels(), "Deleted")}
            >
              Delete all…
            </button>
          </Row>
          <Row title="Reinstall AI libraries" desc="Quits Form Studio and reinstalls PyTorch, diffusers and friends in Terminal. Use it when a local engine fails to load.">
            <button className="btn sm" onClick={reinstallLibs}>Reinstall…</button>
          </Row>
          {reinstall && (
            <div className="set-row wide">
              <div className="note info" style={{ display: "block" }}>
                {reinstall.opened
                  ? "Terminal is reinstalling the AI libraries. Form Studio will quit; reopen it when Terminal says Done."
                  : "Run this in Terminal (Form Studio quits while it runs), then reopen the app:"}
                <div className="kbd" style={{ marginTop: 6, display: "block", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{reinstall.command}</div>
              </div>
            </div>
          )}
        </div>
      </details>
    </>
  );
}

// ---------- storage ----------

const STORAGE_TONES = ["#5e6b2e", "#8f9a5c", "#c2b98f", "#4b6fa8", "#a1372b", "#9a5b12", "#85837a", "#d6d2c6"];

function StorageSection() {
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
  const items = [...(data?.items ?? [])].sort((a, b) => (b.exists ? b.bytes : -1) - (a.exists ? a.bytes : -1));
  const total = items.reduce((n, i) => n + (i.exists ? i.bytes : 0), 0);
  const tone = (id: string) => STORAGE_TONES[(data?.items ?? []).findIndex((i) => i.id === id) % STORAGE_TONES.length];

  return (
    <Section
      id="storage"
      title="Storage"
      intro={data ? <>Form Studio uses <b>{formatBytes(total)}</b> on this Mac, mostly local AI models ({formatBytes(data.total_models_bytes)}). If you only use cloud engines, you can delete them under Local AI.</> : "Measuring folders…"}
      action={<button className="btn sm" disabled={busy} onClick={() => load(true)}>{busy ? "Measuring…" : "Measure again"}</button>}
    >
      {data && (
        <Group>
          {total > 0 && (
            <div className="set-row wide">
              <div className="usage" role="img" aria-label="Disk use by folder">
                {items.filter((i) => i.exists && i.bytes / total > 0.004).map((i) => (
                  <i key={i.id} title={`${i.label}: ${formatBytes(i.bytes)}`} style={{ flexGrow: i.bytes, background: tone(i.id) }} />
                ))}
              </div>
            </div>
          )}
          {items.map((i) => (
            <div key={i.id} className="set-row storage">
              <span className="swatch" style={{ background: i.exists ? tone(i.id) : "transparent" }} aria-hidden />
              <div className="set-text">
                <span className="t">{i.label}</span>
                <span className="d">{i.note}</span>
                <span className="path" title={i.path}>{tidy(i.path)}</span>
              </div>
              <div className="set-ctrl">
                <span className="size">{i.exists ? formatBytes(i.bytes) : "Not yet"}</span>
                <button className="btn sm ghost" disabled={!i.exists} onClick={() => api.openStorage(i.id).catch((e) => toast(e.message, true))}>
                  Show in Finder
                </button>
              </div>
            </div>
          ))}
        </Group>
      )}
    </Section>
  );
}

// ---------- page ----------

export default function SettingsPage({ onSaved }: { onSaved: (s: Settings) => void }) {
  const [s, setS] = useState<Settings | null>(null);
  const [engines, setEngines] = useState<Engines | null>(null);
  const [open, setOpen] = useState<Service | null>(null);
  const [active, setActive] = useState<SectionId>("engines");
  const toast = useToast();

  useEffect(() => {
    api.settings().then(setS);
    api.engines().then(setEngines);
  }, []);

  // highlight the section you're reading in the side list
  useEffect(() => {
    if (!s || !engines) return;
    const root = document.querySelector(".main");
    const obs = new IntersectionObserver(
      (entries) => {
        const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (vis[0]) setActive((vis[0].target as HTMLElement).dataset.section as SectionId);
      },
      { root, rootMargin: "0px 0px -65% 0px" },
    );
    document.querySelectorAll(".set-section").forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, [s, engines]);

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

  const jump = (id: SectionId, then?: () => void) => {
    setActive(id);
    then?.();
    requestAnimationFrame(() => document.getElementById(`set-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };
  const unlock = (e: Engine) => {
    const u = unlockFor(e);
    if (u.service) {
      setOpen(u.service);
      requestAnimationFrame(() => document.getElementById(`acct-${u.service}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
    } else jump(u.section);
  };

  if (!s || !engines) return <div className="page muted">Loading…</div>;

  const restyle = engines.stylize.find((e) => e.id === s.engines.stylize);
  const shape = engines.reconstruct.find((e) => e.id === s.engines.reconstruct);
  const connected = [s.claude.has_key, s.openai.has_key, s.meshy.has_key, s.fal.has_key].filter(Boolean).length;
  const acct = (service: Service) => ({ service, open: open === service, setOpen: (v: boolean) => setOpen(v ? service : null), save });

  return (
    <div className="page settings-page">
      <div className="stack" style={{ gap: 8 }}>
        <span className="label">Settings</span>
        <h1>How Form Studio works for you</h1>
        <p className="lede">Pick the engines you use most, connect cloud accounts if you want them, and manage what's stored on this Mac. You can still switch engines for each object.</p>
      </div>

      <div className="set-summary">
        <button onClick={() => jump("engines")}>
          <span className="label">Restyle</span>
          <span className="v">{restyle?.label ?? "None"}</span>
          {restyle && <KindChip kind={restyle.kind} />}
        </button>
        <button onClick={() => jump("engines")}>
          <span className="label">3D shape</span>
          <span className="v">{shape?.label ?? "None"}</span>
          {shape && <KindChip kind={shape.kind} />}
        </button>
        <button onClick={() => jump("accounts")}>
          <span className="label">Cloud accounts</span>
          <span className="v">{connected} of 4 connected</span>
          <span className="small muted">Keys stay in {s.keychain ? "your Keychain" : "memory"}</span>
        </button>
      </div>

      <div className="settings-layout">
        <nav className="set-nav" aria-label="Settings sections">
          {SECTIONS.map(([id, label]) => (
            <button key={id} className={active === id ? "on" : ""} onClick={() => jump(id)}>{label}</button>
          ))}
        </nav>

        <div className="set-body">
          <Section id="engines" title="Engines" intro="What each new object uses by default. Free engines run on this Mac; cloud engines cost a few cents per object.">
            <Group title="Restyle · photo → your style" note="Makes the styled pictures you choose from">
              <EngineList engines={engines.stylize} value={s.engines.stylize} onChange={(id) => save({ engines: { ...s.engines, stylize: id } }, "Default restyle engine saved")} onUnlock={unlock} />
            </Group>
            <Group title="3D shape · picture → model" note="Turns the chosen picture into a printable shape">
              <EngineList engines={engines.reconstruct} value={s.engines.reconstruct} onChange={(id) => save({ engines: { ...s.engines, reconstruct: id } }, "Default 3D engine saved")} onUnlock={unlock} />
            </Group>
            <Group>
              <Row title="Options per Generate" desc="How many pictures each Generate makes. You can change it on each object.">
                <select className="select" value={Math.min(4, s.candidates)} onChange={(e) => save({ candidates: Number(e.target.value) })}>
                  {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </Row>
            </Group>
          </Section>

          <Section
            id="accounts"
            title="Cloud accounts"
            intro={<>All optional. Each unlocks cloud engines, billed by that service. Keys are stored in {s.keychain ? "your macOS Keychain" : "memory until the app quits (no Keychain found)"}, never in a file.</>}
          >
            <Group>
              <Account
                {...acct("openai")}
                name="OpenAI"
                powers="ChatGPT images restyle · a few cents to ~20¢ per picture"
                where="Create a key at platform.openai.com → API keys. Billing must be set up."
                placeholder="Paste your OpenAI API key (sk-…)"
                hasKey={s.openai.has_key}
                hint={s.openai.key_hint}
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
              </Account>
              <Account
                {...acct("meshy")}
                name="Meshy"
                powers="Meshy image → 3D · uses Meshy credits"
                where="API access needs a Meshy Pro plan or higher. Create a key at meshy.ai → Settings → API."
                placeholder="Paste your Meshy API key (msy_…)"
                hasKey={s.meshy.has_key}
                hint={s.meshy.key_hint}
              >
                <label className="field">
                  <span>Meshy model</span>
                  <select className="select" value={s.meshy.ai_model} onChange={(e) => save({ meshy: { ai_model: e.target.value } })}>
                    <option value="latest">Meshy 7.1 (best shapes, ~20 credits)</option>
                    <option value="meshy-6-lite">Meshy 6 Lite (cheaper, ~5 credits)</option>
                  </select>
                </label>
              </Account>
              <Account
                {...acct("anthropic")}
                name="Anthropic · Claude"
                powers="Claude Sculpt 3D, naming objects, drafting styles"
                where="Create a key at console.anthropic.com → API Keys."
                placeholder="Paste your Anthropic API key (sk-ant-…)"
                hasKey={s.claude.has_key}
                hint={s.claude.key_hint}
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
              </Account>
              <Account
                {...acct("fal")}
                name="fal.ai"
                powers="Nano Banana & FLUX Kontext restyle · TRELLIS & Hunyuan3D 3D"
                where="Create a key at fal.ai → Dashboard → Keys."
                placeholder="Paste your fal.ai key"
                hasKey={s.fal.has_key}
                hint={s.fal.key_hint}
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
              </Account>
            </Group>
          </Section>

          <Section id="local" title="Local AI" intro="Free engines that run on this Mac need these downloads. An interrupted download continues where it stopped. Cloud engines need none of this.">
            <LocalModels s={s} save={save} />
          </Section>

          <Section id="printing" title="Printing" intro="Where your print files go and what's made by default.">
            <Group>
              <Row title="Slicer" desc="Open in slicer sends the 3MF here, with both colours already assigned.">
                <select className="select" value={s.slicer} onChange={(e) => save({ slicer: e.target.value as Settings["slicer"] })}>
                  <option value="bambu">Bambu Studio</option>
                  <option value="orca">OrcaSlicer</option>
                  <option value="prusa">PrusaSlicer</option>
                </select>
              </Row>
              <Row title="Files to export" desc="3MF keeps the two colours for your AMS. STEP is for editing in CAD.">
                <div className="row small">
                  {[["3mf", "3MF"], ["step", "STEP"]].map(([f, label]) => (
                    <label key={f} className="check">
                      <input type="checkbox" checked={s.export_formats.includes(f)} onChange={(e) => save({ export_formats: e.target.checked ? [...s.export_formats, f] : s.export_formats.filter((x) => x !== f) })} />
                      {label}
                    </label>
                  ))}
                </div>
              </Row>
            </Group>
          </Section>

          <StorageSection />
        </div>
      </div>
    </div>
  );
}
