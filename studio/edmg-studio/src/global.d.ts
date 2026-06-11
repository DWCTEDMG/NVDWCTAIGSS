export {};

declare global {
  interface Window {
    edmg?: {
      backendUrl: () => string;
      getBackendUrl?: () => Promise<string>;
      getBackendSettings?: () => Promise<{
        ok: boolean;
        mode: string;
        host: string;
        port: string;
        url: string;
        source: string;
        currentBackendUrl?: string;
      }>;
      getDirectorStatus?: () => Promise<{
        ok: boolean;
        available: boolean;
        managed: boolean;
        serviceUrl: string;
        mcpUrl: string;
        advertisedBaseUrl: string;
        backendUrl: string;
        pid: number | null;
        lastError: string;
        startedAt: string | null;
        packaged: boolean;
      }>;
      setDirectorSettings?: (settings: {
        baseUrl: string;
      }) => Promise<{
        ok: boolean;
        error?: string;
        restartRequired?: boolean;
        available: boolean;
        managed: boolean;
        serviceUrl: string;
        mcpUrl: string;
        advertisedBaseUrl: string;
        backendUrl: string;
        pid: number | null;
        lastError: string;
        startedAt: string | null;
        packaged: boolean;
      }>;
      openExternal?: (url: string) => Promise<void>;
      openPath?: (path: string) => Promise<{ ok: boolean; action?: string; path?: string; error?: string }>;
      showItemInFolder?: (path: string) => Promise<{ ok: boolean; action?: string; path?: string; error?: string }>;
      revealPath?: (path: string) => Promise<{ ok: boolean; action?: string; path?: string; error?: string }>;
      pickFile?: (opts?: any) => Promise<{ ok: boolean; canceled?: boolean; paths?: string[] }>;
      pickDirectory?: (opts?: any) => Promise<{ ok: boolean; canceled?: boolean; path?: string }>;
      getStudioPaths?: () => Promise<{
        ok: boolean;
        platform?: string;
        studioHome: string;
        dataDir: string;
        modelsDir: string;
        cacheRoot: string;
        externalDir: string;
        electronUserData: string;
        sessionData: string;
        logsDir: string;
        storageOverrides?: {
          dataDir?: string;
          modelsDir?: string;
          cacheRoot?: string;
          logsDir?: string;
          externalDir?: string;
        };
        bootstrapConfigPath: string;
        pendingMigration?: any;
        lastMigration?: any;
        source: string;
        storageSource?: string;
      }>;
      getAiSettings?: () => Promise<{
        ok: boolean;
        mode: string;
        provider: string;
        aiBaseUrl: string;
        openaiCompatBaseUrl: string;
        openaiCompatModel: string;
        source: string;
      }>;
      setStudioHome?: (path: string) => Promise<{
        ok: boolean;
        error?: string;
        restartRequired?: boolean;
        migrationPlanned?: boolean;
        migrationSummary?: string;
        studioHome?: string;
        dataDir?: string;
        modelsDir?: string;
        cacheRoot?: string;
        logsDir?: string;
        externalDir?: string;
      }>;
      setStorageSettings?: (settings: {
        studioHome: string;
        dataDir?: string;
        modelsDir?: string;
        cacheRoot?: string;
        logsDir?: string;
        externalDir?: string;
      }) => Promise<{
        ok: boolean;
        error?: string;
        restartRequired?: boolean;
        migrationPlanned?: boolean;
        migrationSummary?: string;
        studioHome?: string;
        dataDir?: string;
        modelsDir?: string;
        cacheRoot?: string;
        logsDir?: string;
        externalDir?: string;
      }>;
      setAiSettings?: (settings: {
        mode: string;
        provider: string;
        aiBaseUrl: string;
        openaiCompatBaseUrl: string;
        openaiCompatModel: string;
      }) => Promise<{
        ok: boolean;
        error?: string;
        restartRequired?: boolean;
        mode?: string;
        provider?: string;
        aiBaseUrl?: string;
        openaiCompatBaseUrl?: string;
        openaiCompatModel?: string;
      }>;
      setBackendSettings?: (settings: {
        mode: string;
        host: string;
        port: string;
        url?: string;
      }) => Promise<{
        ok: boolean;
        error?: string;
        restartRequired?: boolean;
        mode?: string;
        host?: string;
        port?: string;
        url?: string;
        currentBackendUrl?: string;
      }>;
      relaunch?: () => Promise<{ ok: boolean }>;
    };
    __EDMG_BACKEND_URL__?: string;
  }
}
