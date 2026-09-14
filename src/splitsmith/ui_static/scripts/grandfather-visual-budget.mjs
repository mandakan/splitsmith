// One-shot: prepend the visual-budget disable to every file ESLint flags
// under the no-restricted-syntax rule. Re-runnable; skips files that
// already carry the comment. Each PR that rebuilds a page deletes that
// page's comment, so the grandfathered set only shrinks.
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const MARK =
  "/* eslint-disable no-restricted-syntax -- visual budget: remove when this file is rebuilt (spec 2026-09-13 s5) */\n";
let out = "";
try {
  out = execSync("corepack pnpm exec eslint . -f json", { encoding: "utf8", maxBuffer: 1 << 28 });
} catch (e) {
  out = e.stdout; // eslint exits 1 when it reports errors; the JSON is still on stdout
}
const files = JSON.parse(out)
  .filter((r) => r.messages.some((m) => m.ruleId === "no-restricted-syntax"))
  .map((r) => r.filePath);
let added = 0;
for (const f of files) {
  const src = readFileSync(f, "utf8");
  if (src.startsWith(MARK)) continue;
  writeFileSync(f, MARK + src);
  added += 1;
}
console.log(`grandfathered ${added} of ${files.length} flagged files`);
