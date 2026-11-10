import { useState, type FormEvent } from "react";

import { apiFetch, messageOf } from "../../api/client";

export function ResendVerification() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await apiFetch("/api/auth/resend-verification", { method: "POST", json: { email } });
      setSent(true);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }

  // The API answers the same way for unknown and already-verified addresses.
  if (sent) {
    return <p>If that address has an unverified account, a new link is on its way.</p>;
  }
  return (
    <form className="form" onSubmit={submit}>
      <label>
        Send a new link to
        <input
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <button type="submit">Send a new link</button>
    </form>
  );
}
