import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export type StudioTheme = "studio" | "ember" | "aurora" | "atlas";

export type StudioThemeOption = {
  id: StudioTheme;
  label: string;
  description: string;
};

const STORAGE_KEY = "edmg_studio_theme_v1";

export const STUDIO_THEME_OPTIONS: StudioThemeOption[] = [
  {
    id: "studio",
    label: "Studio",
    description: "Current EDMG NVIDIA Workshop look with teal glass and warm highlights.",
  },
  {
    id: "ember",
    label: "Ember",
    description: "Warmer copper-red control room palette.",
  },
  {
    id: "aurora",
    label: "Aurora",
    description: "Cool cyan-blue signal path palette.",
  },
  {
    id: "atlas",
    label: "Atlas",
    description: "Dense green slate palette for longer technical sessions.",
  },
];

function isStudioTheme(value: string): value is StudioTheme {
  return STUDIO_THEME_OPTIONS.some((option) => option.id === value);
}

function readTheme(): StudioTheme {
  const raw = String(localStorage.getItem(STORAGE_KEY) || "studio").trim().toLowerCase();
  return isStudioTheme(raw) ? raw : "studio";
}

function writeTheme(theme: StudioTheme) {
  localStorage.setItem(STORAGE_KEY, theme);
}

function applyTheme(theme: StudioTheme) {
  document.documentElement.dataset.studioTheme = theme;
}

const Ctx = createContext<{ theme: StudioTheme; setTheme: (theme: StudioTheme) => void } | null>(null);

export function StudioAppearanceProvider(props: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<StudioTheme>(() => readTheme());

  const setTheme = useCallback((nextTheme: StudioTheme) => {
    setThemeState(nextTheme);
    writeTheme(nextTheme);
    applyTheme(nextTheme);
  }, []);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) {
        const nextTheme = readTheme();
        setThemeState(nextTheme);
        applyTheme(nextTheme);
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);
  return <Ctx.Provider value={value}>{props.children}</Ctx.Provider>;
}

export function useStudioAppearance() {
  const value = useContext(Ctx);
  if (!value) throw new Error("StudioAppearanceProvider missing");
  return value;
}
