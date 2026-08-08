/**
 * Carry a device user code across the magic-link login redirect (#719).
 *
 * The device flow's verification_uri_complete points at
 * /desktop/approve?code=XXXX-XXXX. With no session, AuthGate bounces to
 * /login; the magic link lands back on "/" with no query string, so the
 * code has to be parked somewhere. sessionStorage, single-use.
 *
 * If the magic link opens in a DIFFERENT browser the stash is gone --
 * that is the conventional device-flow fallback, and /desktop/approve
 * renders an input for the eight characters instead. Taking that path is
 * what lets magic_link.py stay free of a `next` parameter.
 */

const KEY = "splitsmith.deviceApproveCode";

/** The stored form: 8 alphabet characters, hyphenated. Validated on read
 *  so a hand-edited sessionStorage value cannot steer the redirect. */
const USER_CODE_RE = /^[ABCDEFGHJKMNPQRSTVWXYZ23456789]{4}-[ABCDEFGHJKMNPQRSTVWXYZ23456789]{4}$/;

export function stashApproveCode(code: string): void {
  if (!USER_CODE_RE.test(code)) return;
  sessionStorage.setItem(KEY, code);
}

/** Read the stash without consuming it. Pure (no mutation), so -- unlike
 *  ``takeApproveCode`` -- it is safe to call from a render body, including
 *  under StrictMode's double-render on mount: reading the same value twice
 *  changes nothing. ``AuthGate`` uses this to decide, synchronously during
 *  render, whether to redirect -- so the ordinary route tree never mounts
 *  (and never kicks off its own competing redirect, e.g. via
 *  LegacyMatchRedirect) on the same commit where a pickup is pending. */
export function peekApproveCode(): string | null {
  const value = sessionStorage.getItem(KEY);
  return value !== null && USER_CODE_RE.test(value) ? value : null;
}

/** Read and consume the stash. Mutates sessionStorage (read-then-remove),
 *  so this must only ever be called from an effect or an event handler --
 *  never a render body. See ``peekApproveCode`` for the render-safe half
 *  of this pair. */
export function takeApproveCode(): string | null {
  const value = peekApproveCode();
  sessionStorage.removeItem(KEY);
  return value;
}
