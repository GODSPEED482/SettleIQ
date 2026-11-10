// Screen tests opt into jsdom with `// @vitest-environment jsdom`; logic tests stay on node.
// Vitest globals are off, so Testing Library cannot register its own cleanup.
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
