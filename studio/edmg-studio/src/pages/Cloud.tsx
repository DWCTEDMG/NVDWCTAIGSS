import React, { useMemo, useState } from "react";
import { apiPost } from "../components/api";
import { StudioLayoutCustomizer } from "../components/StudioLayoutCustomizer";
import { useStudioPageLayout } from "../components/studioLayout";
import type { PageProps } from "../types/pageProps";

type CloudPanelId = "aws" | "azure" | "lightning" | "result";

export default function Cloud(_props: PageProps) {
  const [bucket, setBucket] = useState("");
  const [awsPrefix, setAwsPrefix] = useState("models");
  const [bundleKey, setBundleKey] = useState("edmg_project_bundle.zip");
  const [azureContainer, setAzureContainer] = useState("edmg-model-cache");
  const [azurePrefix, setAzurePrefix] = useState("models");
  const [lightningOut, setLightningOut] = useState("lightning_bundle");
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  const awsTest = async () => {
    setErr(null); setResult(null);
    try { setResult(await apiPost("/v1/cloud/aws/test", { bucket: bucket || null, prefix: awsPrefix || null })); }
    catch (e: any) { setErr(String(e)); }
  };

  const awsBundle = async () => {
    setErr(null); setResult(null);
    try { setResult(await apiPost("/v1/cloud/aws/bundle", { bucket: bucket || null, key: bundleKey || null })); }
    catch (e: any) { setErr(String(e)); }
  };

  const azureTest = async () => {
    setErr(null); setResult(null);
    try { setResult(await apiPost("/v1/cloud/azure/test", { container: azureContainer || null, prefix: azurePrefix || null })); }
    catch (e: any) { setErr(String(e)); }
  };

  const lightningBundle = async () => {
    setErr(null); setResult(null);
    try { setResult(await apiPost("/v1/cloud/lightning/bundle", { output_dir: lightningOut })); }
    catch (e: any) { setErr(String(e)); }
  };

  const panelDefinitions = useMemo(
    () => [
      {
        id: "aws" as const,
        label: "AWS S3 model cache",
        description: "S3 credential test, model-cache prefix, and optional project bundle upload flow.",
      },
      {
        id: "azure" as const,
        label: "Azure model cache",
        description: "Blob Storage credential test for on-demand model weight caching.",
      },
      {
        id: "lightning" as const,
        label: "Lightning.ai bundle",
        description: "Local bundle generation for Lightning.ai-style backend startup.",
      },
      {
        id: "result" as const,
        label: "Result payload",
        description: "Read-only response output from the latest cloud helper action.",
      },
    ],
    [],
  );
  const {
    profileOptions,
    activeProfile,
    setActiveProfile,
    layoutState,
    visibleOrder,
    movePanel,
    updateHidden,
    resetLayout,
  } = useStudioPageLayout<CloudPanelId>(
    "cloud",
    panelDefinitions.map((panel) => panel.id),
  );
  const panelDefinitionById = useMemo(
    () =>
      Object.fromEntries(
        panelDefinitions.map((definition) => [definition.id, definition]),
      ) as Record<CloudPanelId, (typeof panelDefinitions)[number]>,
    [panelDefinitions],
  );
  const panelControlItems = layoutState.order.map((panelId, index) => ({
    id: panelId,
    label: panelDefinitionById[panelId].label,
    description: panelDefinitionById[panelId].description,
    hidden: layoutState.hidden.includes(panelId),
    canMoveUp: index > 0,
    canMoveDown: index < layoutState.order.length - 1,
  }));

  const panelContent: Record<CloudPanelId, React.ReactNode> = {
    aws: (
      <div className="card">
        <div style={{ fontWeight: 800, marginBottom: 10 }}>AWS</div>
        <div className="small">Optional dependency. Install backend with: pip install -e ".[aws]"</div>
        <div style={{ marginTop: 10 }}>
          <div className="small">S3 bucket</div>
          <input value={bucket} onChange={(e) => setBucket(e.target.value)} placeholder="my-bucket" />
        </div>
        <div style={{ marginTop: 10 }}>
          <div className="small">Model prefix</div>
          <input value={awsPrefix} onChange={(e) => setAwsPrefix(e.target.value)} placeholder="models" />
        </div>
        <div style={{ marginTop: 10 }}>
          <div className="small">Bundle key</div>
          <input value={bundleKey} onChange={(e) => setBundleKey(e.target.value)} />
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={awsTest}>Test credentials</button>
          <button className="secondary" onClick={awsBundle}>Bundle + (optional) upload</button>
        </div>
      </div>
    ),
    azure: (
      <div className="card">
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Azure</div>
        <div className="small">Optional dependency. Install backend with: pip install -e ".[azure]"</div>
        <div style={{ marginTop: 10 }}>
          <div className="small">Blob container</div>
          <input value={azureContainer} onChange={(e) => setAzureContainer(e.target.value)} placeholder="edmg-model-cache" />
        </div>
        <div style={{ marginTop: 10 }}>
          <div className="small">Model prefix</div>
          <input value={azurePrefix} onChange={(e) => setAzurePrefix(e.target.value)} placeholder="models" />
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={azureTest}>Test Azure</button>
        </div>
      </div>
    ),
    lightning: (
      <div className="card">
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Lightning.ai</div>
        <div className="small">Generates a runnable bundle folder (backend + startup script).</div>
        <div style={{ marginTop: 10 }}>
          <div className="small">Output dir</div>
          <input value={lightningOut} onChange={(e) => setLightningOut(e.target.value)} />
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={lightningBundle}>Generate bundle</button>
        </div>
      </div>
    ),
    result: result ? (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Result</div>
        <pre>{JSON.stringify(result, null, 2)}</pre>
      </div>
    ) : null,
  };

  return (
    <div>
      <h1>Cloud</h1>
      <div className="small" style={{ marginTop: 6 }}>
        Reorder or hide cloud helper sections for your own workflow. This only changes the local page layout and does not alter any bundle, upload, or backend behavior.
      </div>
      <StudioLayoutCustomizer
        title="Cloud layout"
        description="Reorder or hide cloud helper panels without changing AWS tests, bundle generation, or upload payloads."
        items={panelControlItems}
        profileOptions={profileOptions}
        activeProfile={activeProfile}
        onSelectProfile={setActiveProfile}
        onMove={movePanel}
        onToggleHidden={updateHidden}
        onReset={resetLayout}
      />
      {err && <div style={{ marginTop: 14, color: "var(--danger)" }}>{err}</div>}
      <div className="grid2" style={{ marginTop: 14 }}>
        {visibleOrder
          .filter((panelId) => panelId !== "result")
          .map((panelId) => (
            <React.Fragment key={panelId}>{panelContent[panelId]}</React.Fragment>
          ))}
      </div>
      {visibleOrder.includes("result") ? panelContent.result : null}
    </div>
  );
}
