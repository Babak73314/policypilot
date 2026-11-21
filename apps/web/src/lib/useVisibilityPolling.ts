import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

/** Polling stops after this long without user activity (ADR-0016, review 01 finding 34). */
export const DEFAULT_IDLE_TIMEOUT_MS = 15 * 60 * 1000;

const ACTIVITY_EVENTS = ["pointerdown", "keydown", "wheel", "touchstart"] as const;

function subscribeToVisibility(onChange: () => void): () => void {
  document.addEventListener("visibilitychange", onChange);
  return () => {
    document.removeEventListener("visibilitychange", onChange);
  };
}

function isDocumentVisible(): boolean {
  return document.visibilityState === "visible";
}

export interface VisibilityPollingOptions {
  /** Whether there is anything to poll for at all. Defaults to true. */
  enabled?: boolean;
  intervalMs: number;
  idleTimeoutMs?: number;
}

export interface VisibilityPolling {
  /** True while polling should run: enabled, the tab is visible and the user is not idle. */
  active: boolean;
  visible: boolean;
  /** True once the user has been inactive for `idleTimeoutMs`. Sticky until `resume()`. */
  idle: boolean;
  /** For TanStack Query: the interval while active, otherwise `false`. */
  refetchInterval: number | false;
  /** Clears the idle stop (wire it to a "Check for updates" button). */
  resume: () => void;
}

/**
 * Decides when a page may poll the API: only while the tab is visible, and never after
 * `idleTimeoutMs` without pointer or keyboard activity. Idle time only accrues while the tab is
 * visible, and returning to the tab counts as activity, because a hidden tab does not poll anyway.
 */
export function useVisibilityPolling({
  enabled = true,
  intervalMs,
  idleTimeoutMs = DEFAULT_IDLE_TIMEOUT_MS,
}: VisibilityPollingOptions): VisibilityPolling {
  const visible = useSyncExternalStore(subscribeToVisibility, isDocumentVisible, () => true);
  const [idle, setIdle] = useState(false);
  const lastActivityRef = useRef(0);

  useEffect(() => {
    if (!enabled || !visible || idle) return;

    // Starting (or returning to the tab, or resuming) counts as activity.
    lastActivityRef.current = Date.now();
    const markActive = () => {
      lastActivityRef.current = Date.now();
    };
    for (const type of ACTIVITY_EVENTS) {
      window.addEventListener(type, markActive, { passive: true, capture: true });
    }

    let timer: ReturnType<typeof setTimeout>;
    const check = () => {
      const remaining = lastActivityRef.current + idleTimeoutMs - Date.now();
      if (remaining <= 0) {
        setIdle(true);
      } else {
        timer = setTimeout(check, remaining);
      }
    };
    timer = setTimeout(check, idleTimeoutMs);

    return () => {
      clearTimeout(timer);
      for (const type of ACTIVITY_EVENTS) {
        window.removeEventListener(type, markActive, { capture: true });
      }
    };
  }, [enabled, visible, idle, idleTimeoutMs]);

  const resume = useCallback(() => {
    setIdle(false);
  }, []);

  const active = enabled && visible && !idle;
  return { active, visible, idle, refetchInterval: active ? intervalMs : false, resume };
}
