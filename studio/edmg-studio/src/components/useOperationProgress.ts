import { useCallback, useEffect, useRef, useState } from "react";
import type { ProgressTone } from "./ProgressBar";

export type OperationProgressState = {
  active: boolean;
  value: number;
  label: string;
  detail: string;
  tone: ProgressTone;
};

const IDLE_STATE: OperationProgressState = {
  active: false,
  value: 0,
  label: "",
  detail: "",
  tone: "accent",
};

export function useOperationProgress() {
  const [progress, setProgress] = useState<OperationProgressState>(IDLE_STATE);
  const timerRef = useRef<number | null>(null);
  const resetTimerRef = useRef<number | null>(null);
  const mountedRef = useRef(true);

  const stopTimer = useCallback(() => {
    if (timerRef.current != null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const stopResetTimer = useCallback(() => {
    if (resetTimerRef.current != null) {
      window.clearTimeout(resetTimerRef.current);
      resetTimerRef.current = null;
    }
  }, []);

  const safeSetProgress = useCallback(
    (next: OperationProgressState | ((current: OperationProgressState) => OperationProgressState)) => {
      if (mountedRef.current) setProgress(next);
    },
    [],
  );

  useEffect(
    () => {
      mountedRef.current = true;
      return () => {
        mountedRef.current = false;
        stopTimer();
        stopResetTimer();
      };
    },
    [stopTimer, stopResetTimer],
  );

  const runOperation = useCallback(
    async <T,>(
      args: {
        label: string;
        detail?: string;
        successDetail?: string;
        successTone?: ProgressTone;
      },
      work: () => Promise<T>,
    ) => {
      stopTimer();
      stopResetTimer();
      safeSetProgress({
        active: true,
        value: 12,
        label: args.label,
        detail: args.detail || "Working...",
        tone: "accent",
      });

      timerRef.current = window.setInterval(() => {
        safeSetProgress((current) =>
          current.active
            ? {
                ...current,
                value: Math.min(92, current.value + (current.value < 48 ? 11 : current.value < 72 ? 7 : 3)),
              }
            : current,
        );
      }, 180);

      try {
        const result = await work();
        stopTimer();
        safeSetProgress({
          active: false,
          value: 100,
          label: args.label,
          detail: args.successDetail || "Complete.",
          tone: args.successTone || "success",
        });
        resetTimerRef.current = window.setTimeout(() => {
          resetTimerRef.current = null;
          safeSetProgress((current) => (current.value === 100 ? IDLE_STATE : current));
        }, 1400);
        return result;
      } catch (error) {
        stopTimer();
        const message = error instanceof Error ? error.message : String(error);
        safeSetProgress({
          active: false,
          value: 100,
          label: args.label,
          detail: message,
          tone: "danger",
        });
        throw error;
      }
    },
    [safeSetProgress, stopResetTimer, stopTimer],
  );

  const clearProgress = useCallback(() => {
    stopTimer();
    stopResetTimer();
    safeSetProgress(IDLE_STATE);
  }, [safeSetProgress, stopResetTimer, stopTimer]);

  return { progress, runOperation, clearProgress };
}
