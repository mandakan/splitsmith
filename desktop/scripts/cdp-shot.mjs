// Screenshot the running app's window over the Chromium DevTools protocol.
//   pnpm start -- --remote-debugging-port=9333   (in another shell)
//   node scripts/cdp-shot.mjs out.png [9333]
// Used for the visual checks; the packaged app never opens the port.
import { writeFileSync } from "node:fs";

const [out = "shot.png", port = "9333"] = process.argv.slice(2);
const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const page = targets.find((t) => t.type === "page");
if (!page) throw new Error("no page target");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
const call = (method, params = {}) =>
  new Promise((resolve) => {
    const id = Math.floor(Math.random() * 1e9);
    const onMsg = (e) => {
      const m = JSON.parse(e.data);
      if (m.id === id) { ws.removeEventListener("message", onMsg); resolve(m.result); }
    };
    ws.addEventListener("message", onMsg);
    ws.send(JSON.stringify({ id, method, params }));
  });
const { data } = await call("Page.captureScreenshot", { format: "png" });
writeFileSync(out, Buffer.from(data, "base64"));
console.log(`${page.url} -> ${out}`);
ws.close();
