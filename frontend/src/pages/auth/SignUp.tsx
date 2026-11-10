import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { apiFetch, messageOf } from "../../api/client";
import { AuthLayout } from "./AuthLayout";

// The API's own limits (RegisterBody in backend/app/api/identity.py).
const MIN_PASSWORD = 12;
const MAX_PASSWORD = 128;

export function SignUp() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/auth/register", { method: "POST", json: { email, password } });
      setSentTo(email);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  // The API answers the same way whether or not the address is already registered.
  if (sentTo) {
    return (
      <AuthLayout title="Check your email">
        <p>
          If <strong>{sentTo}</strong> can be registered, a verification link is on its way.
          Open it to verify your email, then sign in.
        </p>
        <p className="muted">
          <Link to="/sign-in">Back to sign in</Link>
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Create an account">
      <form className="form" onSubmit={submit}>
        <label>
          Email
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD}
            maxLength={MAX_PASSWORD}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <span className="hint">At least {MIN_PASSWORD} characters.</span>
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" disabled={busy}>
          Create account
        </button>
      </form>
      <p className="muted">
        Already registered? <Link to="/sign-in">Sign in</Link>
      </p>
    </AuthLayout>
  );
}
