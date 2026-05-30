/**
 * Magic-link sign-in surface (auth-swap PR2c).
 *
 * Hosted-mode only -- the deployment-mode gate routes anonymous hosted
 * visitors here. The user enters an email; the server e-mails a link
 * (``POST /api/v1/auth/begin``); clicking it hits the server's
 * ``/auth/callback``, which sets the session cookie and redirects back to
 * "/". A failed redemption bounces to ``/login?error=invalid_link``, shown
 * as a banner here.
 *
 * Instrument-panel aesthetic: dark surface, Antonio wordmark, LED-red CTA,
 * the canonical focus-ring input. Accessible: the error is a live region,
 * the LED is never the sole state carrier (text + icon accompany it), and
 * the form is keyboard-operable with visible focus.
 */

import * as React from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { CheckCircle2, Mail } from "lucide-react";

import { Brand } from "@/components/ui/Brand";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function Login() {
  const { status } = useAuth();
  const [params] = useSearchParams();
  const [email, setEmail] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [sentTo, setSentTo] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  // Already signed in (e.g. navigated to /login with a live session) ->
  // send them on to the app.
  if (status === "authed") return <Navigate to="/" replace />;

  const linkError = params.get("error") === "invalid_link";

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const value = email.trim();
    if (!value || !value.includes("@")) {
      setError("Enter a valid email address.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await api.authBegin(value);
      setSentTo(value);
    } catch {
      // begin is always 200 server-side; a throw here means the request
      // never reached the server (offline / 5xx). Don't leak specifics.
      setError("Could not send the link. Check your connection and retry.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center bg-bg px-6 py-12">
      <div className="w-full max-w-[26rem]">
        <div className="mb-8 flex justify-center">
          <Brand serial={<>SS &middot; SIGN IN</>} />
        </div>

        <div className="rounded-lg border border-rule bg-surface p-7 shadow-[0_1px_0_0_var(--color-rule)]">
          {sentTo ? (
            <div className="flex flex-col items-center gap-3 text-center">
              <CheckCircle2 className="size-7 text-led" aria-hidden />
              <h1 className="font-display text-xl font-semibold text-ink">
                Check your email
              </h1>
              <p className="text-sm text-ink-2">
                A sign-in link is on its way to{" "}
                <span className="font-medium text-ink">{sentTo}</span>. The link
                is valid for 15 minutes.
              </p>
              <button
                type="button"
                onClick={() => {
                  setSentTo(null);
                  setError(null);
                }}
                className="mt-1 text-sm text-muted underline-offset-4 transition-colors hover:text-ink hover:underline"
              >
                Use a different email
              </button>
            </div>
          ) : (
            <form onSubmit={onSubmit} noValidate>
              <h1 className="font-display text-xl font-semibold text-ink">
                Sign in
              </h1>
              <p className="mt-1.5 text-sm text-ink-2">
                Enter your email and we'll send a one-time sign-in link. No
                password needed.
              </p>

              {linkError && (
                <p
                  role="alert"
                  className="mt-4 flex items-start gap-2 rounded-md border border-led/40 bg-[color:var(--color-led-tint)] px-3 py-2 text-sm text-led-text"
                >
                  <span aria-hidden>!</span>
                  That sign-in link was invalid or expired. Request a new one
                  below.
                </p>
              )}

              <label
                htmlFor="login-email"
                className="mt-5 block text-xs font-medium uppercase tracking-wide text-muted"
              >
                Email
              </label>
              <input
                id="login-email"
                type="email"
                autoComplete="email"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                disabled={submitting}
                className="mt-1.5 w-full rounded-md border border-rule bg-surface-3 px-3.5 py-2.5 text-sm text-ink outline-none focus:border-led focus:shadow-[0_0_0_3px_var(--color-led-tint)] disabled:opacity-50"
              />

              <p
                role="alert"
                aria-live="polite"
                className="mt-2 min-h-4 text-sm text-led-text"
              >
                {error ?? ""}
              </p>

              <Button
                type="submit"
                disabled={submitting}
                className="mt-3 w-full"
              >
                <Mail className="size-4" aria-hidden />
                {submitting ? "Sending..." : "Send sign-in link"}
              </Button>
            </form>
          )}
        </div>
      </div>
    </main>
  );
}
