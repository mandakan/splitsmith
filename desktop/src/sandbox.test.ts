import { describe, expect, it } from "vitest";

import { sandboxBlocked } from "./sandbox";

describe("sandboxBlocked", () => {
  const cases: Array<[boolean, boolean, boolean, boolean]> = [
    // appImage, noSandbox, userNsWorks, blocked
    [true, true, false, true], // AppRun fell back: refuse
    [true, true, true, false], // user passed --no-sandbox where it works: their call
    [true, false, false, false], // sandbox on: Electron itself decides
    [true, false, true, false],
    [false, true, false, false], // .deb / dev run: never ours to refuse
    [false, true, true, false],
    [false, false, false, false],
    [false, false, true, false],
  ];
  it.each(cases)("appImage=%s noSandbox=%s userNs=%s -> %s", (appImage, noSandbox, works, blocked) => {
    expect(sandboxBlocked({ appImage, noSandbox, userNsWorks: () => works })).toBe(blocked);
  });
  it("only probes namespaces when the other two hold", () => {
    let probed = 0;
    const probe = () => {
      probed += 1;
      return false;
    };
    sandboxBlocked({ appImage: false, noSandbox: true, userNsWorks: probe });
    sandboxBlocked({ appImage: true, noSandbox: false, userNsWorks: probe });
    expect(probed).toBe(0);
  });
});
