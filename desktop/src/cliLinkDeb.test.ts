import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SNIPPET = path.join(__dirname, "..", "linux", "cli-link.sh");

function run(fn: string, bin: string, opt: string): void {
  const r = spawnSync("bash", ["-c", `source '${SNIPPET}' && ${fn}`], {
    env: { PATH: "/usr/bin:/bin", SPLITSMITH_BIN_DIR: bin, SPLITSMITH_OPT_DIR: opt },
    encoding: "utf8",
  });
  expect(r.status).toBe(0);
}

function dirs() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "deb-"));
  const bin = path.join(root, "bin");
  const opt = path.join(root, "opt", "Splitsmith");
  fs.mkdirSync(bin, { recursive: true });
  fs.mkdirSync(path.join(opt, "resources", "python", "bin"), { recursive: true });
  return { bin, opt, link: path.join(bin, "splitsmith"), src: path.join(opt, "resources", "python", "bin", "splitsmith") };
}

describe("deb CLI link", () => {
  it("links on install and again on reinstall", () => {
    const d = dirs();
    run("splitsmith_cli_link", d.bin, d.opt);
    run("splitsmith_cli_link", d.bin, d.opt);
    expect(fs.readlinkSync(d.link)).toBe(d.src);
  });
  it("leaves a foreign file and a foreign link alone, on install and removal", () => {
    const d = dirs();
    fs.writeFileSync(d.link, "#!/bin/sh\n");
    run("splitsmith_cli_link", d.bin, d.opt);
    run("splitsmith_cli_unlink", d.bin, d.opt);
    expect(fs.readFileSync(d.link, "utf8")).toBe("#!/bin/sh\n");
    fs.rmSync(d.link);
    fs.symlinkSync("/home/me/.local/bin/splitsmith", d.link);
    run("splitsmith_cli_link", d.bin, d.opt);
    run("splitsmith_cli_unlink", d.bin, d.opt);
    expect(fs.readlinkSync(d.link)).toBe("/home/me/.local/bin/splitsmith");
  });
  it("removes its own link", () => {
    const d = dirs();
    run("splitsmith_cli_link", d.bin, d.opt);
    run("splitsmith_cli_unlink", d.bin, d.opt);
    expect(fs.lstatSync(d.link, { throwIfNoEntry: false })).toBeUndefined();
  });
});
