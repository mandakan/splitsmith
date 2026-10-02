import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

const LIB = path.join(__dirname, "..", "lib", "target.sh");

function hostTarget(s: string, m: string): { code: number | null; out: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "uname-"));
  fs.writeFileSync(path.join(dir, "uname"), `#!/bin/sh\n[ "$1" = -s ] && echo ${s} || echo ${m}\n`, { mode: 0o755 });
  const r = spawnSync("bash", ["-c", `source '${LIB}' && host_target`], {
    env: { PATH: `${dir}:/usr/bin:/bin` },
    encoding: "utf8",
  });
  return { code: r.status, out: r.stdout.trim() };
}

describe("host_target", () => {
  it("is macos-aarch64 on an arm64 Mac, so a flagless macOS build is unchanged", () => {
    expect(hostTarget("Darwin", "arm64")).toEqual({ code: 0, out: "macos-aarch64" });
  });
  it("is linux-x86_64 on x86_64 Linux", () => {
    expect(hostTarget("Linux", "x86_64")).toEqual({ code: 0, out: "linux-x86_64" });
  });
  it("refuses anything else", () => {
    expect(hostTarget("Darwin", "x86_64").code).toBe(1);
    expect(hostTarget("Linux", "aarch64").code).toBe(1);
  });
});
