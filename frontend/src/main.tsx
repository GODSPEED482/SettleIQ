import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { PermissionsProvider } from "./permissions/PermissionsContext";
import { SessionProvider } from "./session/SessionContext";
import "./styles.css";

// The session sits outside permissions: a tenant can only be selected inside a session.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <PermissionsProvider>
          <App />
        </PermissionsProvider>
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
);
