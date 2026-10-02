// GET /desktop/latest.json
//
// The desktop app's update feed: the newest released version and where
// to get it. Answers our own shape so shipped apps keep working when the
// download moves behind a purchase; only this function changes then.
//
//   { "version": "0.41.0", "url": "https://github.com/.../releases/tag/v0.41.0" }
//
// Reads the GitHub releases list rather than /releases/latest: that
// endpoint returns the newest non-prerelease by date, which can be an
// ffmpeg source release (tag ffmpeg-macos-arm64-*), not an app version.
// Only tags shaped v<major>.<minor>.<patch> count, and only once the
// requesting platform's install assets are attached: release-please
// publishes the release before desktop/release.sh has built and uploaded
// the app, and an app told about a version must be able to download it.
// Cached at the edge for ten minutes; an app checks once per launch.
const RELEASES = "https://api.github.com/repos/mandakan/splitsmith/releases?per_page=20";
const APP_TAG = /^v(\d+\.\d+\.\d+)$/;
const CACHE_SECONDS = 600;

function json(status, body, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });
}

// Which assets must be attached before a release is announced to an app
// on that platform. Shipped macOS apps send no ?platform=, so "mac" is
// the default and its rule never changes.
const REQUIRED_ASSETS = {
  mac: [/\.dmg$/],
  linux: [/\.AppImage$/, /\.deb$/],
};

export function pickLatest(releases, platform = "mac") {
  const required = REQUIRED_ASSETS[platform];
  if (!required) throw new Error(`unknown platform ${platform}`);
  const versions = [];
  for (const r of releases) {
    if (r.draft || r.prerelease) continue;
    const m = APP_TAG.exec(r.tag_name || "");
    if (!m) continue;
    const names = (r.assets || []).map((a) => a.name || "");
    if (!required.every((re) => names.some((n) => re.test(n)))) continue;
    versions.push({ version: m[1], url: r.html_url, key: m[1].split(".").map(Number) });
  }
  versions.sort((a, b) => b.key[0] - a.key[0] || b.key[1] - a.key[1] || b.key[2] - a.key[2]);
  return versions.length ? { version: versions[0].version, url: versions[0].url } : null;
}

export async function onRequestGet({ request }) {
  const requested = new URL(request.url).searchParams.get("platform");
  if (requested !== null && requested !== "linux") return json(400, { error: `unknown platform ${requested}` });
  const platform = requested ?? "mac";
  let releases;
  try {
    const res = await fetch(RELEASES, {
      headers: { accept: "application/vnd.github+json", "user-agent": "splitsmith-update-feed" },
      cf: { cacheTtl: CACHE_SECONDS, cacheEverything: true },
    });
    if (!res.ok) return json(502, { error: `github ${res.status}` });
    releases = await res.json();
  } catch (err) {
    return json(502, { error: String(err && err.message ? err.message : err) });
  }
  const latest = pickLatest(Array.isArray(releases) ? releases : [], platform);
  if (!latest) return json(404, { error: "no app release" });
  return json(200, latest, { "cache-control": `public, max-age=${CACHE_SECONDS}` });
}
