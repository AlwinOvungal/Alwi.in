import { ReactNode, useEffect, useRef, useState } from "react";
import { api, BenchReport, BlenderStatus, CleanupItem, Engine, Engines, formatBytes, formatSeconds, ModelPack, ModelQueue, QUALITIES, Settings, StorageItem, Task } from "./api";
import { ErrorCard, KindChip, Progress, Switch, useToast } from "./ui";

type Save = (patch: Parameters<typeof api.saveSettings>[0], msg?: string) => Promise<void>;
type Service = "anthropic" | "openai" | "meshy" | "fal";

const SECTIONS = [
  ["engines", "Engines"],
  ["accounts", "Cloud accounts"],
  ["local", "Local AI"],
  ["speed", "Speed test"],
  ["printing", "Printing"],
  ["storage", "Storage"],
] as const;
type SectionId = (typeof SECTIONS)[number][0];

const FAL_ENGINES: [string, string, string][] = [
  ["fal-nano-banana", "Nano Banana (restyle)", "fal-ai/nano-banana/edit"],
  ["fal-kontext", "FLUX Kontext (restyle)", "fal-ai/flux-kontext/dev"],
  ["fal-hunyuan3", "Hunyuan3D 3.1 Rapid (3D)", "fal-ai/hunyuan-3d/v3.1/rapid/image-to-3d"],
  ["fal-hunyuan3-pro", "Hunyuan3D 3.1 Pro (3D, and with 3 views)", "fal-ai/hunyuan-3d/v3.1/pro/image-to-3d"],
  ["fal-tripo", "Tripo H3.1 (3D)", "tripo3d/h3.1/image-to-3d"],
  ["fal-tripo-multi", "Tripo H3.1 with 3 views (3D)", "tripo3d/h3.1/multiview-to-3d"],
  ["fal-trellis2", "TRELLIS 2 (3D)", "fal-ai/trellis-2"],
  ["fal-meshy", "Meshy 6 (3D)", "fal-ai/meshy/v6/image-to-3d"],
  ["fal-trellis", "TRELLIS, older (3D)", "fal-ai/trellis"],
  ["fal-hunyuan", "Hunyuan3D-2 mini, older (3D)", "fal-ai/hunyuan3d/v2/mini"],
];

/** Which account or section unlocks an engine that isn't available yet. */
function unlockFor(e: Engine): { service?: Service; section: SectionId; label: string } {
  if (e.id.startsWith("fal-")) return { service: "fal", section: "accounts", label: "Connect fal.ai" };
  if (e.id.startsWith("openai")) return { service: "openai", section: "accounts", label: "Connect OpenAI" };
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

function EngineList({ engines: all, value, onChange, onUnlock }: { engines: Engine[]; value: string; onChange: (id: string) => void; onUnlock: (e: Engine) => void }) {
  const engines = all.filter((e) => !e.legacy || e.id === value); // older engines only while still in use
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
        <Row title="Restyle quality" desc={QUALITIES.find((q) => q.id === s.sdxl_quality)?.hint}>
          <div className="seg" role="radiogroup" aria-label="Restyle quality">
            {QUALITIES.map((q) => (
              <button key={q.id} type="button" role="radio" aria-checked={s.sdxl_quality === q.id} className={s.sdxl_quality === q.id ? "on" : ""} onClick={() => save({ sdxl_quality: q.id }, `${q.label} quality`)}>
                {q.label}
              </button>
            ))}
          </div>
        </Row>
        <Row
          title="Keep models loaded"
          desc={
            s.keep_loaded === "auto"
              ? `${s.memory.ram_gb ? `Your Mac has ${s.memory.ram_gb} GB. ` : ""}Up to ${s.memory.budget_gb} GB of models stay in memory, so SDXL and TripoSR don't reload for every object.${s.memory.loaded.length ? ` In memory now: ${s.memory.loaded.join(", ")}.` : ""}`
              : "Only one model in memory at a time: uses the least memory, but each step reloads its model."
          }
        >
          <select className="select" value={s.keep_loaded} onChange={(e) => save({ keep_loaded: e.target.value as Settings["keep_loaded"] })}>
            <option value="auto">Automatic</option>
            <option value="one">One at a time</option>
          </select>
        </Row>
        <Row title="Get engines ready in the background" desc="Loads your default local engines when the app opens and when you add a photo, so the first option starts sooner.">
          <Switch label="" checked={s.warm_up} onChange={(v) => save({ warm_up: v })} />
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

// ---------- speed test ----------

const MEASURES: [string, string][] = [
  ["first_option", "Photo → first option"],
  ["first_to_3mf", "Photo → 3MF, using the first option right away"],
  ["all_to_3mf", "Photo → 3MF after all options"],
  ["slider", "One print-slider change"],
];

function BenchResult({ r }: { r: BenchReport }) {
  const ids = Object.keys(r.variants);
  const med = (v: string, m: string) => r.variants[v].warm[m]?.median ?? r.variants[v].cold?.[m] ?? null;
  const allOk = ids.every((v) => r.variants[v].checks_ok);
  return (
    <Group
      title={`Latest result · ${r.when.replace("_", " ").replace(/-(\d\d)-(\d\d)$/, ":$1")}`}
      note={`${r.mac.ram_gb} GB Mac · ${r.setup.restyle} → ${r.setup.shape} · ${r.setup.photos} photo${r.setup.photos === 1 ? "" : "s"} × ${r.setup.runs} runs`}
    >
      <div className="set-row wide">
        <div className="bench-scroll">
          <table className="bench-table">
            <thead>
              <tr>
                <th>Median, models in memory</th>
                {ids.map((v) => <th key={v}>{r.variants[v].label}</th>)}
              </tr>
            </thead>
            <tbody>
              {MEASURES.map(([m, label]) => (
                <tr key={m}>
                  <td>{label}</td>
                  {ids.map((v, i) => {
                    const a = med(ids[0], m);
                    const b = med(v, m);
                    // only for times long enough that the difference isn't noise
                    const x = i > 0 && a && b && a >= 2 ? a / b : null;
                    return (
                      <td key={v}>
                        {formatSeconds(b)}
                        {x && x >= 1.1 ? <span className="fast"> · {x.toFixed(1)}×</span> : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
              <tr>
                <td>First run of the session (photo → first option)</td>
                {ids.map((v) => <td key={v}>{formatSeconds(r.variants[v].cold?.first_option)}</td>)}
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div className="set-row wide">
        {allOk ? (
          <div className="note ok">Every print file passed: watertight, the right height, and parts on the right filaments.</div>
        ) : (
          <div className="note bad">Some print files didn't pass the checks. Open the full report for details.</div>
        )}
      </div>
      {r.sheet && (
        <div className="set-row wide">
          <div className="stack" style={{ gap: 6 }}>
            <span className="small muted">The options each way made, so you can check the look as well as the speed:</span>
            <img className="bench-sheet" src={`/api/bench/files/options.png?v=${r.when}`} alt="Options made by each variant" />
          </div>
        </div>
      )}
      <div className="set-row">
        <div className="set-text">
          <span className="t">Full report</span>
          <span className="d">Every step's median and p90 times, in a file you can share.</span>
        </div>
        <div className="set-ctrl">
          <a className="btn sm" href="/api/bench/files/report.md" target="_blank" rel="noreferrer">Open report</a>
        </div>
      </div>
    </Group>
  );
}

function SpeedTest() {
  const [st, setSt] = useState<{ task: Task | null; latest: BenchReport | null; photos: number } | null>(null);
  const [size, setSize] = useState<"quick" | "full">("quick");
  const [compare, setCompare] = useState(true);
  const toast = useToast();
  const load = () => api.bench().then(setSt).catch(() => {});
  useEffect(() => {
    load();
  }, []);
  const running = !!st?.task && (st.task.status === "running" || st.task.status === "queued");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [running]);
  const start = async () => {
    try {
      setSt(await api.startBench(size, compare));
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  return (
    <Section
      id="speed"
      title="Speed test"
      intro="Times the whole flow on this Mac (photo → options → 3D shape → 3MF) with copies of your most recent photos. Your objects aren't changed."
    >
      <Group>
        <Row title="How thorough" desc={size === "quick" ? "1 photo, 2 runs. Roughly 10–20 minutes with local SDXL." : "3 photos, 3 runs each. Roughly an hour with local SDXL; most reliable."}>
          <div className="seg" role="radiogroup" aria-label="How thorough">
            <button type="button" className={size === "quick" ? "on" : ""} onClick={() => setSize("quick")}>Quick check</button>
            <button type="button" className={size === "full" ? "on" : ""} onClick={() => setSize("full")}>Full</button>
          </div>
        </Row>
        <Row title="Compare" desc="Also runs the old way (one model at a time, Best) and Quick quality, so you see the difference in time and in the pictures. Quick downloads ~400 MB once.">
          <Switch label="" checked={compare} onChange={setCompare} />
        </Row>
        {running && st?.task ? (
          <div className="set-row wide">
            <Progress task={st.task} onCancel={() => api.cancelBench().then(load)} />
          </div>
        ) : (
          <Row
            title={st && st.photos === 0 ? "Add an object first" : "Run the speed test"}
            desc={st && st.photos === 0 ? "The test uses your photos. Create an object in the Library, then come back." : "Uses your default engines. Other work waits while it runs."}
          >
            <button className="btn sm primary" disabled={!st || st.photos === 0} onClick={start}>Run speed test</button>
          </Row>
        )}
        {st?.task?.status === "error" && (
          <div className="set-row wide"><ErrorCard title="The speed test stopped" task={st.task} onRetry={start} /></div>
        )}
      </Group>
      {st?.latest && <BenchResult r={st.latest} />}
    </Section>
  );
}

// ---------- Blender ----------

function BlenderRow({ s, save }: { s: Settings; save: Save }) {
  const [b, setB] = useState<BlenderStatus | null>(null);
  const [looking, setLooking] = useState(false);
  const [path, setPath] = useState(s.blender_path ?? "");
  const toast = useToast();
  const load = () => api.blender().then(setB).catch(() => {});
  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const installing = !!b?.task && (b.task.status === "running" || b.task.status === "queued");
  useEffect(() => {
    if (!installing) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [installing]); // eslint-disable-line react-hooks/exhaustive-deps
  const look = async () => {
    setLooking(true);
    try {
      setB(await api.findBlender());
    } finally {
      setLooking(false);
    }
  };
  const install = async () => {
    try {
      setB(await api.installBlender());
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  return (
    <div className="set-row wide">
      <div className="set-text">
        <span className="t">Blender</span>
        <span className="d">
          {b === null
            ? "Looking for Blender…"
            : b.found
              ? <>Ready: Blender {b.version}{b.kind === "bpy" ? " inside Form Studio" : <> at <span className="kbd">{b.path}</span></>}. Used by the <b>Blender clay</b> 3D finish.</>
              : b.can_install
                ? <>Not installed. The <b>Blender clay</b> finish needs Blender: install it inside Form Studio (about 230 MB, no Blender app), or use the Blender app if you have it.</>
                : <>Not found. The <b>Blender clay</b> finish needs Blender (free): <a href={b.download} target="_blank" rel="noreferrer">download it</a>, put it in Applications, then press Look again.</>}
        </span>
      </div>
      {installing && b?.task ? (
        <Progress task={b.task} />
      ) : (
        <>
          {b?.task?.status === "error" && <ErrorCard title="Blender didn't install" task={b.task} onRetry={install} />}
          <div className="row" style={{ gap: 8 }}>
            {b && !b.found && b.can_install && <button className="btn sm primary" onClick={install}>Install Blender</button>}
            <input className="input grow" placeholder="Optional: path to the Blender app" value={path}
              onChange={(e) => setPath(e.target.value)}
              onBlur={() => path !== (s.blender_path ?? "") && save({ blender_path: path.trim() }, "Blender path saved").then(look)} />
            <button className="btn sm" disabled={looking} onClick={look}>{looking ? "Looking…" : "Look again"}</button>
          </div>
        </>
      )}
    </div>
  );
}

// ---------- storage ----------

const STORAGE_TONES = ["#5e6b2e", "#8f9a5c", "#c2b98f", "#4b6fa8", "#a1372b", "#9a5b12", "#85837a", "#d6d2c6"];

function CleanUp({ onDone }: { onDone: () => void }) {
  const [data, setData] = useState<{ items: CleanupItem[]; total: number } | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const show = (d: { items: CleanupItem[]; total: number }) => {
    setData(d);
    setPicked(new Set(d.items.filter((i) => !i.careful).map((i) => i.id))); // models only when you tick them
  };
  useEffect(() => {
    api.cleanup().then(show).catch(() => {});
  }, []);
  const chosen = (data?.items ?? []).filter((i) => picked.has(i.id));
  const size = chosen.reduce((n, i) => n + i.bytes, 0);
  const run = async () => {
    if (!confirm(`Remove ${formatBytes(size)} of files Form Studio doesn't need?\n\n${chosen.map((i) => `• ${i.label}`).join("\n")}\n\nYour objects and styles stay.`)) return;
    setBusy(true);
    try {
      const r = await api.runCleanup(chosen.map((i) => i.id));
      toast(`Freed ${formatBytes(r.freed)}`);
      show(r);
      onDone();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const toggle = (id: string) => setPicked((p) => {
    const n = new Set(p);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  });
  return (
    <Group title="Clean up" note={data ? (data.items.length ? `${formatBytes(data.total)} can go` : "Nothing to remove") : "Looking…"}>
      {!data && <Row title="Looking for files Form Studio doesn't need…" desc="Leftovers, caches, older speed tests and models no engine uses." />}
      {data && data.items.length === 0 && (
        <Row title="Nothing to clean up" desc="No leftovers, caches or unused models right now. Your objects and styles are never touched." />
      )}
      {data?.items.map((i) => (
        <label key={i.id} className="set-row clean-row">
          <input type="checkbox" checked={picked.has(i.id)} onChange={() => toggle(i.id)} />
          <span className="set-text">
            <span className="t">{i.label}{i.careful ? <span className="small muted"> · not ticked: you might want these</span> : null}</span>
            <span className="d">{i.note}</span>
          </span>
          <span className="set-ctrl"><span className="size">{formatBytes(i.bytes)}</span></span>
        </label>
      ))}
      {data && data.items.length > 0 && (
        <Row title={`Remove ${formatBytes(size)}`} desc="Your objects (photos, options, shapes, print files) and styles are never touched.">
          <button className="btn sm primary" disabled={busy || !chosen.length} onClick={run}>{busy ? "Removing…" : "Remove selected"}</button>
        </Row>
      )}
    </Group>
  );
}

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
      <CleanUp onDone={() => load(true)} />
      {data && (
        <Group title="Folders">
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

/** Chat assistants: ChatGPT (and Claude) can drive Form Studio over MCP. */
function McpCard({ s }: { s: Settings }) {
  const toast = useToast();
  const token = s.mcp?.token ?? "";
  const url = `${location.origin}/mcp/${token}/`;
  if (!token) return null;
  return (
    <Group title="Chat assistants (MCP)" note="Ask ChatGPT or Claude to make a magnet from a picture; Form Studio does the work on this Mac.">
      <div className="stack" style={{ gap: 8, padding: "10px 0" }}>
        <label className="field">
          <span>This Mac's MCP address (the last part is a secret: share it with no one)</span>
          <div className="row" style={{ gap: 8 }}>
            <input className="input grow" readOnly value={url} onFocus={(e) => e.target.select()} />
            <button className="btn sm" onClick={() => navigator.clipboard.writeText(url).then(() => toast("Address copied"))}>Copy</button>
          </div>
        </label>
        <span className="small muted">
          <b>Claude Code</b> on this Mac: <code>claude mcp add --transport http form-studio {"<address>"}</code>.{" "}
          <b>ChatGPT</b> can only reach public addresses, so first share the port with a tunnel, e.g.{" "}
          <code>ngrok http {location.port || "80"}</code> — then in ChatGPT: Settings → Connectors → Advanced →
          Developer mode → Create, with the tunnel's address + <code>/mcp/{"<secret>"}/</code> and No authentication.
          Keep the tunnel's address private too: with it, anyone can use this app. The assistant can then make stand
          toys, magnets and keychains from pictures, check progress, change part colours and export print files.
        </span>
      </div>
    </Group>
  );
}

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
            <Group title="3 views · picture → front, side, back" note="Optional, in the Shape step: lets Hunyuan3D, Tripo and Meshy see the whole object">
              <EngineList engines={engines.views} value={s.engines.views ?? ""} onChange={(id) => save({ engines: { ...s.engines, views: id } }, "Default view maker saved")} onUnlock={unlock} />
              <Row title="Make 3 views automatically" desc="Picking an option (Use this) also draws its front, side and back, ready for the 3D shape.">
                <Switch label="" checked={!!s.views_auto} onChange={(v) => save({ views_auto: v })} />
              </Row>
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
                    <option value="gpt-image-2.6-sunburst">GPT Image 2.6 Sunburst (newest)</option>
                    <option value="gpt-image-2.6-flare">GPT Image 2.6 Flare (newest, lighter)</option>
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
                powers="Nano Banana & FLUX Kontext restyle · Hunyuan3D 3.1, Tripo, TRELLIS 2 & Meshy 3D"
                where="Create a key at fal.ai → Dashboard → Keys. You pay per use, from your fal.ai balance."
                placeholder="Paste your fal.ai key"
                hasKey={s.fal.has_key}
                hint={s.fal.key_hint}
              >
                <p className="small muted" style={{ margin: 0 }}>
                  3D engines make the shape only: colours come from your picture, so you never pay for textures.
                </p>
                <label className="field">
                  <span>Hunyuan3D 3.1 quality</span>
                  <select className="select" value={s.fal.hunyuan3_quality} onChange={(e) => save({ fal: { hunyuan3_quality: e.target.value as Settings["fal"]["hunyuan3_quality"] } }, "Saved")}>
                    <option value="rapid">Rapid: about $0.23 a shape (recommended)</option>
                    <option value="pro">Pro: about $0.38 a shape, finer detail</option>
                  </select>
                </label>
                <label className="field">
                  <span>Tripo detail</span>
                  <select className="select" value={s.fal.tripo_detail} onChange={(e) => save({ fal: { tripo_detail: e.target.value as Settings["fal"]["tripo_detail"] } }, "Saved")}>
                    <option value="standard">Standard: about $0.20 a shape</option>
                    <option value="detailed">Detailed: about $0.40 a shape</option>
                  </select>
                </label>
                <label className="field">
                  <span>TRELLIS 2 resolution</span>
                  <select className="select" value={s.fal.trellis2_resolution} onChange={(e) => save({ fal: { trellis2_resolution: Number(e.target.value) as Settings["fal"]["trellis2_resolution"] } }, "Saved")}>
                    <option value={512}>512: about $0.25 a shape</option>
                    <option value={1024}>1024: about $0.30 a shape (recommended)</option>
                    <option value={1536}>1536: about $0.35 a shape</option>
                  </select>
                </label>
                <p className="small muted" style={{ margin: 0 }}>With 3 views, Hunyuan3D uses Pro (+$0.15). Prices are fal.ai's and can change.</p>
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
            <McpCard s={s} />
          </Section>

          <Section id="local" title="Local AI" intro="Free engines that run on this Mac need these downloads. An interrupted download continues where it stopped. Cloud engines need none of this.">
            <LocalModels s={s} save={save} />
          </Section>

          <SpeedTest />

          <Section id="printing" title="Printing" intro="Where your print files go and what's made by default.">
            <Group>
              <BlenderRow s={s} save={save} />
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
