import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { PermissionsProvider } from "./permissions/PermissionsContext";
import "./styles.css";

// TODO(G1 1.11 Suprava): add a session provider here once OPEN-14 settles the auth transport.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <PermissionsProvider>
        <App />
      </PermissionsProvider>
    </BrowserRouter>
  </StrictMode>,
);
