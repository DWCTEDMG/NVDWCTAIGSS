import React, { Suspense, lazy, useEffect, useMemo, useState } from "react";
import Sidebar from "./components/Sidebar";
import { apiGet, getBackendUrl, getBackendUrlAsync } from "./components/api";

import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";
import Setup from "./pages/Setup";
import { isStudioForgeEnabled } from "./features";
import {
  getPageLoadingDetails,
  isPage,
  preloadLikelyNextPages,
  type Page,
} from "./pageRouting";

const Workspace = lazy(() => import("./pages/Workspace"));
const Timeline = lazy(() => import("./pages/Timeline"));
const Render = lazy(() => import("./pages/Render"));
const RenderQueue = lazy(() => import("./pages/RenderQueue"));
const Outputs = lazy(() => import("./pages/Outputs"));
const Cloud = lazy(() => import("./pages/Cloud"));
const Settings = lazy(() => import("./pages/Settings"));
const Models = lazy(() => import("./pages/Models"));
const EdmgDirector = lazy(() => import("./pages/EdmgDirector"));
const AiPlannerLab = lazy(() => import("./pages/AiPlannerLab"));
const ReactiveLab = lazy(() => import("./pages/ReactiveLab"));
const StudioForge = lazy(() => import("./pages/StudioForge"));

function getInitialPage(): Page {
  if (typeof window === "undefined") return "dashboard";
  const raw = new URLSearchParams(window.location.search).get("page");
  return raw && isPage(raw) ? raw : "dashboard";
}

function getForcedPage(): Page | null {
  if (typeof window === "undefined") return null;
  const raw = new URLSearchParams(window.location.search).get("page");
  return raw && isPage(raw) ? raw : null;
}

function PageLoadingFallback({ page }: { page: Page }) {
  const loading = getPageLoadingDetails(page);
  return (
    <div className="card">
      <div style={{ fontWeight: 800, marginBottom: 8 }}>Loading studio screen</div>
      <div className="small">
        Preparing <b>{loading.label}</b>.
      </div>
      <div className="small" style={{ marginTop: 8, opacity: 0.84 }}>
        {loading.detail}
      </div>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>(getInitialPage);
  const [forcedPage] = useState<Page | null>(getForcedPage);
  const [backendUrl, setBackendUrl] = useState<string>("");
  const [config, setConfig] = useState<any>(null);
  const [backendConfigError, setBackendConfigError] = useState<string>("");
  const [setupChecked, setSetupChecked] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const url = await getBackendUrlAsync();
        if (alive) setBackendUrl(url);
      } catch {
        if (alive) setBackendUrl(getBackendUrl());
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!backendUrl) return;
    apiGet("/v1/config")
      .then((nextConfig) => {
        setConfig(nextConfig);
        setBackendConfigError("");
      })
      .catch((error: any) => {
        setConfig(null);
        setBackendConfigError(String(error?.message ?? error));
      });
  }, [backendUrl]);

  useEffect(() => {
    if (!backendUrl || setupChecked) return;
    apiGet("/v1/setup/status")
      .then((s) => {
        const plannerRequired = !!s?.nvidia_profile?.enabled;
        const backendBundleOk = !!s?.backend_bundle?.ok;
        const ffmpegOk = !!s?.ffmpeg?.ok;
        const plannerOk = !plannerRequired || !!s?.nvidia_planner?.ok;
        const need = !(backendBundleOk && ffmpegOk && plannerOk);
        if (need && !forcedPage) setPage("setup" as any);
        setSetupChecked(true);
      })
      .catch(() => setSetupChecked(true));
  }, [backendUrl, forcedPage, setupChecked]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    let canceled = false;
    const runPreload = () => {
      if (canceled) return;
      preloadLikelyNextPages(page);
    };

    const requestIdle = window.requestIdleCallback;
    if (typeof requestIdle === "function") {
      const idleId = requestIdle(runPreload, { timeout: 1200 });
      return () => {
        canceled = true;
        window.cancelIdleCallback?.(idleId);
      };
    }

    const timeoutId = window.setTimeout(runPreload, 150);
    return () => {
      canceled = true;
      window.clearTimeout(timeoutId);
    };
  }, [page]);

  const commonProps = useMemo(() => ({ backendUrl, config }), [backendUrl, config]);

  if (!backendUrl) {
    return (
      <div className="app-shell">
        <Sidebar page={page} onNavigate={setPage} />
        <div className="main">
          <div className="card">
            <div style={{ fontWeight: 800, marginBottom: 8 }}>Connecting to Studio backend</div>
            <div className="small">Resolving the active backend target before loading workspace screens.</div>
          </div>
        </div>
      </div>
    );
  }

  let content: React.ReactNode = null;
  if (page === "dashboard") content = <Dashboard {...commonProps} onNavigate={setPage as any} />;
  if (page === "projects") content = <Projects {...commonProps} />;
  if (page === "workspace") content = <Workspace {...commonProps} onNavigate={setPage as any} />;
  if (page === "timeline") content = <Timeline {...commonProps} onNavigate={setPage as any} />;
  if (page === "render") content = <Render {...commonProps} onNavigate={setPage as any} />;
  if (page === "queue") content = <RenderQueue {...commonProps} onNavigate={setPage as any} />;
  if (page === "outputs") content = <Outputs {...commonProps} onNavigate={setPage as any} />;
  if (page === "cloud") content = <Cloud {...commonProps} />;
  if (page === "settings") content = <Settings {...commonProps} />;
  if (page === "setup") content = <Setup onNavigate={setPage as any} />;
  if (page === "models") content = <Models {...commonProps} />;
  if (page === "directorLab")
    content = <EdmgDirector {...commonProps} onNavigate={setPage as any} />;
  if (page === "plannerLab")
    content = <AiPlannerLab {...commonProps} onNavigate={setPage as any} />;
  if (page === "reactiveLab")
    content = <ReactiveLab {...commonProps} onNavigate={setPage as any} />;
  if (page === "studioForge" && isStudioForgeEnabled())
    content = <StudioForge {...commonProps} onNavigate={setPage as any} />;
  if (page === "studioForge" && !isStudioForgeEnabled())
    content = <Dashboard {...commonProps} onNavigate={setPage as any} />;

  const mainClassName = page === "timeline" ? "main main--timeline" : "main";

  return (
    <div className="app-shell">
      <Sidebar page={page} onNavigate={setPage} />
      <div className={mainClassName}>
        {backendConfigError ? (
          <div className="card" style={{ marginBottom: 14, borderColor: "var(--warning, #b58900)" }}>
            <div style={{ fontWeight: 800, marginBottom: 8 }}>Backend connection needs attention</div>
            <div className="small" style={{ marginBottom: 8 }}>
              Studio resolved <b>{backendUrl}</b> but could not load `/v1/config` from it.
            </div>
            <div className="small" style={{ opacity: 0.84 }}>
              If you intended to attach the desktop GUI to an external backend, open Settings and review Desktop Backend mode, host, and port. Error: {backendConfigError}
            </div>
          </div>
        ) : null}
        <Suspense fallback={<PageLoadingFallback page={page} />}>
          {content}
        </Suspense>
      </div>
    </div>
  );
}
