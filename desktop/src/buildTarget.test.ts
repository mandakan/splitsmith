import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

const LIB = path.join(__dirname, "..", "lib", "target.sh");

function hostTarget(s: string, m: string): { code: number | null; out: string; err: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "uname-"));
  fs.writeFileSync(path.join(dir, "uname"), `#!/bin/sh\n[ "$1" = -s ] && echo ${s} || echo ${m}\n`, { mode: 0o755 });
  const r = spawnSync("bash", ["-c", `source '${LIB}' && host_target`], {
    env: { PATH: `${dir}:/usr/bin:/bin` },
    encoding: "utf8",
  });
  return { code: r.status, out: r.stdout.trim(), err: r.stderr };
}

function lib(...args: string[]): { code: number | null; out: string } {
  const r = spawnSync("bash", ["-c", `source '${LIB}' && "$@"`, "--", ...args], { encoding: "utf8" });
  return { code: r.status, out: r.stdout.trim() };
}

describe("host_target", () => {
  it("is macos-aarch64 on an arm64 Mac, so a flagless macOS build is unchanged", () => {
    expect(hostTarget("Darwin", "arm64")).toMatchObject({ code: 0, out: "macos-aarch64" });
  });
  it("is linux-x86_64 on x86_64 Linux", () => {
    expect(hostTarget("Linux", "x86_64")).toMatchObject({ code: 0, out: "linux-x86_64" });
  });
  it("refuses anything else", () => {
    for (const [s, m] of [
      ["Darwin", "x86_64"],
      ["Linux", "aarch64"],
    ]) {
      const r = hostTarget(s, m);
      expect(r.code).toBe(1);
      expect(r.out).toBe("");
      expect(r.err).toMatch(/unsupported build host/);
    }
  });
});

describe("parse_target_flag", () => {
  it("maps --mac and --linux to their targets", () => {
    expect(lib("parse_target_flag", "--mac")).toEqual({ code: 0, out: "macos-aarch64" });
    expect(lib("parse_target_flag", "--linux")).toEqual({ code: 0, out: "linux-x86_64" });
  });
  it("exits non-zero on anything else", () => {
    for (const flag of ["--win", "--target", "mac", ""]) {
      const r = lib("parse_target_flag", flag);
      expect(r.code).not.toBe(0);
      expect(r.out).toBe("");
    }
  });
});

describe("sha256_verify", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sha-"));
  const file = path.join(dir, "blob");
  fs.writeFileSync(file, "splitsmith\n");
  // printf 'splitsmith\n' | sha256sum
  const digest = "2da195fb7af7bdccf21f4cdb6ccf67d71ad4be730b803e5216b392071856b8c6";

  it("passes on the file's digest", () => {
    expect(lib("sha256_verify", digest, file).code).toBe(0);
  });
  it("fails when one character of the digest differs", () => {
    const wrong = `${digest.slice(0, -1)}${digest.endsWith("6") ? "7" : "6"}`;
    expect(lib("sha256_verify", wrong, file).code).not.toBe(0);
  });
  // A Mac without coreutils has no sha256sum; the fallback is a Linux path.
  const sha256sum = spawnSync("bash", ["-c", "command -v sha256sum"], { encoding: "utf8" }).stdout.trim();
  it.skipIf(!sha256sum)("falls back to sha256sum where there is no shasum", () => {
    const bin = fs.mkdtempSync(path.join(os.tmpdir(), "nosha-"));
    fs.symlinkSync(sha256sum, path.join(bin, "sha256sum"));
    const run = (sha: string) =>
      spawnSync("/bin/bash", ["-c", `source '${LIB}' && command -v shasum || sha256_verify "$@"`, "--", sha, file], {
        env: { PATH: bin },
        encoding: "utf8",
      }).status;
    expect(run(digest)).toBe(0);
    expect(run(digest.replace(/^./, digest[0] === "0" ? "1" : "0"))).not.toBe(0);
  });
});
