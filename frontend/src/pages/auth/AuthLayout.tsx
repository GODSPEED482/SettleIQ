import type { ReactNode } from "react";

export function AuthLayout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="brand">SettleIQ</div>
        <h1>{title}</h1>
        {children}
      </section>
    </main>
  );
}
