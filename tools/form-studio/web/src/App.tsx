import { useEffect, useState } from "react";
import { api, Settings } from "./api";
import JobPage from "./JobPage";
import Library from "./Library";
import SettingsPage from "./SettingsPage";
import { NewStyle, StyleEditor, StylesList } from "./StylesPage";
import { ToastProvider, useRoute } from "./ui";

export default function App() {
  const route = useRoute();
  const [settings, setSettings] = useState<Settings | null>(null);
  useEffect(() => {
    api.settings().then(setSettings).catch(() => {});
  }, [route[0]]);

  const page = route[0] ?? "";
  return (
    <ToastProvider>
      <div className="shell">
        <header className="topbar">
          <a href="#/" className="brand">
            <span className="brand-mark" aria-hidden="true" />
            Form Studio
          </a>
          <nav className="nav" aria-label="Main">
            <a href="#/" className={page === "" || page === "job" ? "active" : ""}>
              Library
            </a>
            <a href="#/styles" className={page === "styles" ? "active" : ""}>
              Styles
            </a>
            <a href="#/settings" className={page === "settings" ? "active" : ""}>
              Settings
            </a>
          </nav>
          <span className="spacer" />
          {settings && (
            <span className="chip" title="Where local models run">
              <span className="dot" />
              {settings.resolved_device === "mps" ? "Apple GPU" : settings.resolved_device.toUpperCase()}
              {settings.fast_mode ? " · fast" : ""}
            </span>
          )}
        </header>
        <main className="main">
          {page === "job" && route[1] ? (
            <JobPage id={route[1]} key={route[1]} />
          ) : page === "styles" && route[1] === "new" ? (
            <NewStyle />
          ) : page === "styles" && route[1] ? (
            <StyleEditor id={route[1]} key={route[1]} />
          ) : page === "styles" ? (
            <StylesList />
          ) : page === "settings" ? (
            <SettingsPage onSaved={setSettings} />
          ) : (
            <Library />
          )}
        </main>
      </div>
    </ToastProvider>
  );
}
