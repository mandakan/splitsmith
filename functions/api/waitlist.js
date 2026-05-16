// POST /api/waitlist
//
// Stores an email in the WAITLIST KV namespace for the marketing
// "Hosted Splitsmith - coming soon" modal. Intentionally minimal: no
// double-opt-in, no newsletter sending. When we are ready to actually
// email the list, dump it (`pnpm waitlist:list` + `pnpm waitlist:get`)
// and import into Resend / Buttondown / etc.
//
// Request body:
//   { email: string, hp?: string }
//     hp is a honeypot field. Real users leave it empty; bots fill it.
//     A non-empty hp value returns 200 without writing.
//
// Storage shape in KV:
//   key   = "email:<lowercased-email>"
//   value = JSON { ts, ip_hash, ua, source }
//
//   key   = "rl:<ip_hash>"   (rate limit counter, TTL = 1h)
//   value = "<count>"

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const RATE_LIMIT_MAX = 5;
const RATE_LIMIT_WINDOW_SECONDS = 3600;
const MAX_EMAIL_LEN = 254;

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

async function hashIp(ip) {
  const data = new TextEncoder().encode(ip);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 16);
}

export async function onRequestPost({ request, env }) {
  if (!env.WAITLIST) {
    return json(500, { error: "waitlist storage not configured" });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json(400, { error: "invalid json" });
  }

  // Honeypot: pretend success without writing so bots get no signal.
  if (typeof body.hp === "string" && body.hp.trim() !== "") {
    return json(200, { ok: true });
  }

  const email =
    typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  if (!email || email.length > MAX_EMAIL_LEN || !EMAIL_RE.test(email)) {
    return json(400, { error: "invalid email" });
  }

  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const ipHash = await hashIp(ip);

  const rlKey = `rl:${ipHash}`;
  const current = parseInt((await env.WAITLIST.get(rlKey)) || "0", 10);
  if (current >= RATE_LIMIT_MAX) {
    return json(429, { error: "rate limited" });
  }
  await env.WAITLIST.put(rlKey, String(current + 1), {
    expirationTtl: RATE_LIMIT_WINDOW_SECONDS,
  });

  const key = `email:${email}`;
  const existing = await env.WAITLIST.get(key);
  if (existing) {
    return json(200, { ok: true, already: true });
  }

  await env.WAITLIST.put(
    key,
    JSON.stringify({
      ts: new Date().toISOString(),
      ip_hash: ipHash,
      ua: request.headers.get("user-agent") || "",
      source: "marketing-waitlist",
    }),
  );

  return json(200, { ok: true });
}

export async function onRequest({ request }) {
  return new Response("method not allowed", {
    status: 405,
    headers: { allow: "POST" },
  });
}
