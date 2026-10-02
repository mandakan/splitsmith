import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SNIPPET = path.join(__dirname, "..", "linux", "cli-link.sh");
const MARKER = "# splitsmith-desktop CLI wrapper";

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
  return {
    bin,
    opt,
    link: path.join(bin, "splitsmith"),
    src: path.join(opt, "resources", "python", "bin", "splitsmith"),
  };
}

function expectWrapper(d: ReturnType<typeof dirs>): void {
  const st = fs.lstatSync(d.link);
  expect(st.isSymbolicLink()).toBe(false);
  expect(st.isFile()).toBe(true);
  expect(st.mode & 0o777).toBe(0o755);
  const body = fs.readFileSync(d.link, "utf8");
  expect(body.split("\n")).toContain(MARKER);
  expect(body).toContain(d.src);
  expect(body).toContain(path.join(d.opt, "resources", "bin", "ffmpeg"));
  expect(body).toContain(path.join(d.opt, "resources", "bin", "ffprobe"));
}

describe("deb CLI wrapper", () => {
  it("writes an executable wrapper on install and again on reinstall", () => {
    const d = dirs();
    run("splitsmith_cli_link", d.bin, d.opt);
    expectWrapper(d);
    const first = fs.readFileSync(d.link, "utf8");
    run("splitsmith_cli_link", d.bin, d.opt);
    expectWrapper(d);
    expect(fs.readFileSync(d.link, "utf8")).toBe(first);
    expect(fs.readdirSync(d.bin)).toEqual(["splitsmith"]);
  });
  it("replaces a link into opt from an older install, and removes it on uninstall", () => {
    const d = dirs();
    fs.symlinkSync(d.src, d.link);
    run("splitsmith_cli_link", d.bin, d.opt);
    expectWrapper(d);
    fs.rmSync(d.link);
    fs.symlinkSync(d.src, d.link);
    run("splitsmith_cli_unlink", d.bin, d.opt);
    expect(fs.lstatSync(d.link, { throwIfNoEntry: false })).toBeUndefined();
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
  it("removes its own wrapper", () => {
    const d = dirs();
    run("splitsmith_cli_link", d.bin, d.opt);
    run("splitsmith_cli_unlink", d.bin, d.opt);
    expect(fs.lstatSync(d.link, { throwIfNoEntry: false })).toBeUndefined();
  });
  it("runs the bundled CLI with the bundled ffmpeg unless the caller set one", () => {
    const d = dirs();
    fs.writeFileSync(
      d.src,
      '#!/bin/sh\necho "ffmpeg=$SPLITSMITH_FFMPEG"\necho "ffprobe=$SPLITSMITH_FFPROBE"\necho "args=$*"\n',
      { mode: 0o755 },
    );
    run("splitsmith_cli_link", d.bin, d.opt);
    const bundled = spawnSync(d.link, ["detect", "--video", "a b.mp4"], {
      env: { PATH: "/usr/bin:/bin" },
      encoding: "utf8",
    });
    expect(bundled.status).toBe(0);
    expect(bundled.stdout).toBe(
      [
        `ffmpeg=${path.join(d.opt, "resources", "bin", "ffmpeg")}`,
        `ffprobe=${path.join(d.opt, "resources", "bin", "ffprobe")}`,
        "args=detect --video a b.mp4",
        "",
      ].join("\n"),
    );
    const mine = spawnSync(d.link, ["--help"], {
      env: { PATH: "/usr/bin:/bin", SPLITSMITH_FFMPEG: "/usr/local/bin/ffmpeg" },
      encoding: "utf8",
    });
    expect(mine.status).toBe(0);
    expect(mine.stdout).toContain("ffmpeg=/usr/local/bin/ffmpeg\n");
    expect(mine.stdout).toContain(`ffprobe=${path.join(d.opt, "resources", "bin", "ffprobe")}\n`);
  });
});
