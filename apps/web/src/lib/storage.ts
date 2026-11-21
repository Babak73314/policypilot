/**
 * Defensive wrappers around Web Storage. Storage can be unavailable (private mode, blocked
 * cookies, quota exceeded), and that must never break the page, so every access is guarded.
 */

export type StorageArea = "local" | "session";

function getStorage(area: StorageArea): Storage | null {
  try {
    return area === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

/** Reads and parses a JSON value, returning null when it is missing, unreadable or fails `guard`. */
export function readJson<T>(area: StorageArea, key: string, guard: (value: unknown) => value is T): T | null {
  try {
    const raw = getStorage(area)?.getItem(key);
    if (raw === null || raw === undefined) return null;
    const value: unknown = JSON.parse(raw);
    return guard(value) ? value : null;
  } catch {
    return null;
  }
}

/** Writes a JSON value. Returns false when storage is unavailable. */
export function writeJson(area: StorageArea, key: string, value: unknown): boolean {
  try {
    const storage = getStorage(area);
    if (!storage) return false;
    storage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

export function removeItem(area: StorageArea, key: string): void {
  try {
    getStorage(area)?.removeItem(key);
  } catch {
    // Nothing to clean up when storage is unavailable.
  }
}
