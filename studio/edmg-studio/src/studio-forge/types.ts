export type StudioForgeCapability =
  | "backend"
  | "ollama"
  | "openaiCompatible"
  | "nvidiaKit"
  | "nvidiaNim"
  | "nvidiaRiva"
  | "nvidiaTriton"
  | "nvidiaOmniverse"
  | "comfyui"
  | "ffmpeg"
  | "internalRenderer"
  | "edmgCore";

export type StudioForgeBridgeKind =
  | "metadataExport"
  | "renderHandoff"
  | "controlBridge";

export type StudioForgeBridgeTransport =
  | "fileExport"
  | "http"
  | "websocket"
  | "osc"
  | "remoteControl";

export type StudioForgeTemplateKind =
  | "page"
  | "panel"
  | "workflow"
  | "renderPreset"
  | "modelProfile";

export type StudioForgeTemplate = {
  id: string;
  name: string;
  kind: StudioForgeTemplateKind;
  description: string;
  requiredCapabilities: StudioForgeCapability[];
  optionalCapabilities?: StudioForgeCapability[];
  destructive: false;
  status: "preview";
};

export type StudioForgeRecipe = {
  id: string;
  name: string;
  description: string;
  stages: string[];
  requiredCapabilities: StudioForgeCapability[];
  optionalCapabilities?: StudioForgeCapability[];
  destructive: false;
  status: "preview";
};

export type StudioForgeBridge = {
  id: string;
  name: string;
  kind: StudioForgeBridgeKind;
  description: string;
  transports: StudioForgeBridgeTransport[];
  outputs: string[];
  previewPayload: Record<string, unknown>;
  limitations: string;
  requiredCapabilities: StudioForgeCapability[];
  optionalCapabilities?: StudioForgeCapability[];
  destructive: false;
  status: "preview";
};
