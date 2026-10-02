/**
 * The AppImage's sandbox refusal, pure part. electron-builder's AppRun
 * adds --no-sandbox when ``unshare -Ur true`` fails (Ubuntu 24.04+ with
 * AppArmor's userns restriction); left alone the app would then run
 * unsandboxed without saying so. main.ts turns that case into a dialog
 * and a quit. A .deb run ($APPIMAGE unset) is never refused: its setuid
 * helper and AppArmor profile carry the sandbox.
 */
export const RELEASES_PAGE = "https://github.com/mandakan/splitsmith/releases";
export const BLOCKED_MESSAGE = "This system blocks the sandbox an AppImage needs";
export const BLOCKED_DETAIL =
  "Install the .deb from the release page instead. It sets the sandbox up during installation.";

export function sandboxBlocked(o: { appImage: boolean; noSandbox: boolean; userNsWorks: () => boolean }): boolean {
  return o.appImage && o.noSandbox && !o.userNsWorks();
}
