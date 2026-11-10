import { vi } from "vitest";

// Node 22's own localStorage global shadows jsdom's, so screen tests install an in-memory one.
export function stubLocalStorage(): Storage {
  const items = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return items.size;
    },
    clear: () => items.clear(),
    getItem: (key) => items.get(key) ?? null,
    key: (index) => [...items.keys()][index] ?? null,
    removeItem: (key) => void items.delete(key),
    setItem: (key, value) => void items.set(key, String(value)),
  };
  vi.stubGlobal("localStorage", storage);
  return storage;
}
