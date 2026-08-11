/**
 * Share-mount detection for components that render on both the operator
 * routes and the anonymous /share/:token routes. The server whitelist
 * is the security backstop; this only decides whether to render write
 * affordances at all (a write from a share mount would be silently
 * rewritten onto the share prefix by scopeRequestPath and 404).
 */
export function isShareView(pathname: string): boolean {
  return /^\/share\//.test(pathname);
}
