import { vi } from "vitest";

// jsdom lays nothing out, and React Flow measures its viewport. These stand-ins let a screen
// that holds a canvas render; what the graph draws is tested on the pure helpers instead.
export function stubLayout() {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    "DOMMatrixReadOnly",
    class {
      m22 = 1;
      constructor(transform?: string) {
        const scale = transform?.match(/scale\(([0-9.]+)\)/)?.[1];
        this.m22 = scale ? Number(scale) : 1;
      }
    },
  );
}
