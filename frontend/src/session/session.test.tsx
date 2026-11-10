// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { SignIn } from "../pages/auth/SignIn";
import { tokenFromHash, VerifyEmail } from "../pages/auth/VerifyEmail";
import { apiError, mockApi } from "../test/api";
import { RequireSession } from "./RequireSession";
import { SessionProvider, sessionStatusFor } from "./SessionContext";

const SIGNED_IN = { status: 200, body: { tenants: [] } };
const SIGNED_OUT = apiError(401, "UNAUTHENTICATED", "Authentication required.");

afterEach(() => {
  vi.unstubAllGlobals();
});

function Where() {
  const location = useLocation();
  return <p>at {location.pathname + location.hash}</p>;
}

function renderAt(path: string, children: ReactNode) {
  return render(
    <StrictMode>
      <MemoryRouter initialEntries={[path]}>
        <SessionProvider>{children}</SessionProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}

describe("sessionStatusFor", () => {
  it("maps API failures to session states", () => {
    expect(sessionStatusFor(new ApiError(401, "UNAUTHENTICATED", ""))).toBe("signed-out");
    expect(sessionStatusFor(new ApiError(403, "EMAIL_NOT_VERIFIED", ""))).toBe("unverified");
    expect(sessionStatusFor(new TypeError("offline"))).toBe("unavailable");
  });
});

describe("RequireSession", () => {
  const app = (
    <Routes>
      <Route path="/sign-in" element={<Where />} />
      <Route path="*" element={<RequireSession>workspace</RequireSession>} />
    </Routes>
  );

  it("sends a signed-out visitor to sign in", async () => {
    mockApi({ "GET /api/me/tenants": SIGNED_OUT });
    renderAt("/team", app);
    expect(await screen.findByText("at /sign-in")).toBeTruthy();
  });

  it("asks an unverified user to verify first", async () => {
    mockApi({ "GET /api/me/tenants": apiError(403, "EMAIL_NOT_VERIFIED") });
    renderAt("/team", app);
    expect(await screen.findByRole("heading", { name: "Verify your email" })).toBeTruthy();
    expect(screen.queryByText("workspace")).toBeNull();
  });

  it("renders the app for a verified session", async () => {
    mockApi({ "GET /api/me/tenants": SIGNED_IN });
    renderAt("/team", app);
    expect(await screen.findByText("workspace")).toBeTruthy();
  });
});

describe("SignIn", () => {
  const app = (
    <Routes>
      <Route path="/sign-in" element={<SignIn />} />
      <Route path="*" element={<Where />} />
    </Routes>
  );

  function fill(email: string, password: string) {
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  }

  it("signs in and returns to the app", async () => {
    const api = mockApi({
      "GET /api/me/tenants": [SIGNED_OUT, SIGNED_OUT, SIGNED_IN],
      "POST /api/auth/sign-in": { status: 200, body: { user: {} } },
    });
    renderAt("/sign-in", app);
    fill("a@example.com", "correct horse battery");
    expect(await screen.findByText("at /")).toBeTruthy();
    expect(api.sent("POST /api/auth/sign-in")).toEqual([
      { email: "a@example.com", password: "correct horse battery" },
    ]);
  });

  it("shows the API's message on failure", async () => {
    mockApi({
      "GET /api/me/tenants": SIGNED_OUT,
      "POST /api/auth/sign-in": apiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect."),
    });
    renderAt("/sign-in", app);
    fill("a@example.com", "wrong");
    expect((await screen.findByRole("alert")).textContent).toBe("Email or password is incorrect.");
  });
});

describe("VerifyEmail", () => {
  const app = (
    <Routes>
      <Route path="/verify-email" element={<VerifyEmail />} />
    </Routes>
  );

  it("reads the token from the fragment", () => {
    expect(tokenFromHash("#token=abc")).toBe("abc");
    expect(tokenFromHash("")).toBeNull();
  });

  it("spends the token once, even under StrictMode", async () => {
    const api = mockApi({
      "GET /api/me/tenants": SIGNED_IN,
      "POST /api/auth/verify-email": { status: 200, body: { status: "verified" } },
    });
    renderAt("/verify-email#token=abc", app);
    expect(await screen.findByRole("heading", { name: "Email verified" })).toBeTruthy();
    expect(api.sent("POST /api/auth/verify-email")).toEqual([{ token: "abc" }]);
  });

  it("offers a new link when the token fails", async () => {
    mockApi({
      "GET /api/me/tenants": SIGNED_OUT,
      "POST /api/auth/verify-email": apiError(400, "INVALID_TOKEN"),
    });
    renderAt("/verify-email#token=used", app);
    expect(await screen.findByRole("button", { name: "Send a new link" })).toBeTruthy();
  });
});
