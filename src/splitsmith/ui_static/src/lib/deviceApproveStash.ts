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

export function takeApproveCode(): string | null {
  const value = sessionStorage.getItem(KEY);
  sessionStorage.removeItem(KEY);
  return value !== null && USER_CODE_RE.test(value) ? value : null;
}
