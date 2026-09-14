# UX PR 2: Shell and Primitives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the one-bar shell (breadcrumb in the bar, ambient progress strip, grouped nav with the approved renames) and the ten primitives every later page is built from, enforced by an ESLint rule, without rebuilding any page.

**Architecture:** Primitives are small CVA components in `components/ui/` exported from the barrel; the shell change is two new portal slots on `RootLayout`'s `ShellChromeValue` (`crumbSlot`, `stripSlot`) that `MatchShell` and `JobsSurface` fill, so no shell gains a second chrome mechanism and no second jobs poller appears. The lint rule fires on class strings and is grandfathered per file.

**Tech Stack:** React 19, Tailwind 4, class-variance-authority, vitest + Testing Library, ESLint 10 (`no-restricted-syntax` with AST selectors).

**Spec:** `docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md` sections 3.3, 5, 6. Mockup: https://claude.ai/code/artifact/2437d7ec-c7dc-444b-a747-1a0823c7d301 (approved 2026-09-14 with: build as shown; renames and Jobs-row removal now; grandfather per file; keep a muted "next" marker).

## Global Constraints

- `corepack pnpm typecheck / lint / test / build` in `src/splitsmith/ui_static`; bare `pnpm` is not on PATH. No prettier: the repo has no prettier config and running it reformats unrelated lines.
- No new dependencies.
- ASCII punctuation in copy and comments.
- `uv run` rewrites `uv.lock`: `git checkout uv.lock` before staging; never `git add -A`. Playwright MCP writes `.playwright-mcp/` in the repo root; delete before committing.
- Visual budget (spec section 5) applies to every primitive: Antonio only in `PageHeader` and `Button variant="primary"`; the only tracked-caps style is `Label` (mono 11 px, 0.08 em); no leading zeros on counts; glow only on live state; hairlines `white/7` and `white/14`.
- No page is rebuilt. Existing pages keep compiling through the old primitives, which stay exported (marked deprecated in the barrel) until their last consumer migrates.
- Commit messages end with the session's attribution lines.
- Visual verification uses the local demo match: `uv run python <scratchpad>/seed_demo_match.py <scratchpad>/demo-match` then `uv run splitsmith ui --project <scratchpad>/demo-match --skip-system-check --no-browser --port 5174` (the server rebuilds `dist/` on start when sources are newer; wait for `/api/health`). Open `http://127.0.0.1:5174/`, pick the match, screenshot with the Playwright MCP. Stop the server with `pkill -9 -f "splitsmith ui --project"` (a plain `pkill` returns 144 and short-circuits `&&` chains).

---

### Task 1: Branch and the lint rule (grandfathered)

**Files:**
- Modify: `src/splitsmith/ui_static/eslint.config.js`
- Create: `src/splitsmith/ui_static/scripts/grandfather-visual-budget.mjs`
- Modify: every file the rule flags under `src/pages/**` and `src/components/**` (excluding `src/components/ui/**`): one leading disable comment.

**Interfaces:**
- Produces: ESLint rule id `no-restricted-syntax` configured with four selectors; the per-file comment `/* eslint-disable no-restricted-syntax -- visual budget: remove when this file is rebuilt (spec 2026-09-13 s5) */`.

- [ ] **Step 1: Branch**

```bash
cd /home/mathias/work/splitsmith && git checkout main && git pull --ff-only && git checkout -b ux/pr2-shell-primitives
```

- [ ] **Step 2: Add the rule**

In `eslint.config.js`, add a second config object after the existing one:

```js
  // Visual budget (docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md s5).
  // Pages and feature components get type and spacing from components/ui,
  // never from arbitrary Tailwind values. Files that predate the rule carry
  // a file-level disable that the PR rebuilding that page deletes.
  {
    files: ["src/pages/**/*.{ts,tsx}", "src/components/**/*.{ts,tsx}"],
    ignores: ["src/components/ui/**", "**/*.test.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "Literal[value=/text-\\[[0-9.]+(rem|px)\\]/]",
          message: "Arbitrary text size. Use a type role from components/ui (PageHeader, Label, Stat, or the Tailwind text-* scale).",
        },
        {
          selector: "TemplateElement[value.raw=/text-\\[[0-9.]+(rem|px)\\]/]",
          message: "Arbitrary text size. Use a type role from components/ui.",
        },
        {
          selector: "Literal[value=/tracking-\\[/], TemplateElement[value.raw=/tracking-\\[/]",
          message: "Arbitrary letter-spacing. Label is the only tracked-caps style.",
        },
        {
          selector: "Literal[value=/\\bfont-display\\b/], TemplateElement[value.raw=/\\bfont-display\\b/]",
          message: "Antonio is PageHeader and Button variant=\"primary\" only.",
        },
        {
          selector: "Literal[value=/\\bbg-led\\b.*\\btext-bg\\b/], TemplateElement[value.raw=/\\bbg-led\\b.*\\btext-bg\\b/]",
          message: "Cream-on-saturated-red fails contrast; use Button variant=\"primary\".",
        },
      ],
    },
  },
```

- [ ] **Step 3: Run lint to see the flagged files**

Run: `cd src/splitsmith/ui_static && corepack pnpm lint 2>&1 | grep -c "no-restricted-syntax"`
Expected: a large number (about 1400). Then list files: `corepack pnpm lint 2>&1 | grep -B1 "no-restricted-syntax" | grep "^/" | sort -u | wc -l` -- about 75.

- [ ] **Step 4: Write the grandfather script**

`scripts/grandfather-visual-budget.mjs`:

```js
// One-shot: prepend the visual-budget disable to every file ESLint flags.
// Re-runnable; skips files that already carry the comment.
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const MARK = "/* eslint-disable no-restricted-syntax -- visual budget: remove when this file is rebuilt (spec 2026-09-13 s5) */\n";
let out = "";
try {
  out = execSync("corepack pnpm exec eslint . -f json", { encoding: "utf8", maxBuffer: 1 << 28 });
} catch (e) {
  out = e.stdout; // eslint exits 1 on errors; the JSON is still on stdout
}
const files = JSON.parse(out)
  .filter((r) => r.messages.some((m) => m.ruleId === "no-restricted-syntax"))
  .map((r) => r.filePath);
for (const f of files) {
  const src = readFileSync(f, "utf8");
  if (src.startsWith(MARK)) continue;
  writeFileSync(f, MARK + src);
}
console.log(`grandfathered ${files.length} files`);
```

Run: `cd src/splitsmith/ui_static && node scripts/grandfather-visual-budget.mjs && corepack pnpm lint 2>&1 | tail -1`
Expected: `grandfathered ~75 files`, then lint reports 0 errors (the 49 pre-existing warnings remain).

- [ ] **Step 5: Prove the rule bites on new code**

Create `src/pages/__budget_probe.tsx` containing `export const x = "text-[0.5625rem]";`, run `corepack pnpm exec eslint src/pages/__budget_probe.tsx`, expect one `no-restricted-syntax` error, delete the file.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/eslint.config.js src/splitsmith/ui_static/scripts/grandfather-visual-budget.mjs src/splitsmith/ui_static/src
git commit -m "chore(ui): lint rule for the visual budget, grandfathered per file

Arbitrary text sizes, trackings, font-display and the bg-led/text-bg pair
are errors in pages and feature components from now on. Every file that
already violates carries one disable comment naming the spec; the PR that
rebuilds a page deletes its comment, so the count only goes down."
```

---

### Task 2: `Label` and the `numeral` utility

**Files:**
- Create: `src/components/ui/Label.tsx`, `src/components/ui/Label.test.tsx`
- Modify: `src/styles/index.css` (add `@utility numeral`), `src/components/ui/index.ts`

**Interfaces:**
- Produces: `<Label tone="muted" | "accent" | "live" | "done">` renders a `<span>` with `font-mono text-[11px] font-medium uppercase tracking-[0.08em]` (arbitrary values are allowed inside `components/ui/`); in dev, warns once per text when children exceed three words. CSS utility `numeral` = `font-family: var(--font-mono); font-weight: 500; font-variant-numeric: tabular-nums lining-nums; font-feature-settings: "tnum" 1, "lnum" 1;`.

- [ ] **Step 1: Failing tests**

`Label.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Label } from "./Label";

describe("Label", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the one tracked-caps style", () => {
    render(<Label>Avg split</Label>);
    const el = screen.getByText("Avg split");
    expect(el.className).toMatch(/font-mono/);
    expect(el.className).toMatch(/uppercase/);
    expect(el.className).toMatch(/tracking-\[0\.08em\]/);
    expect(el.className).toMatch(/text-\[11px\]/);
  });

  it("warns in dev when asked to carry a sentence", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    render(<Label>Footage attached and participating in this match</Label>);
    expect(warn).toHaveBeenCalledWith(expect.stringMatching(/Label.*three words/));
  });

  it("tones", () => {
    render(<Label tone="accent">Results</Label>);
    expect(screen.getByText("Results").className).toMatch(/accent-mode/);
  });
});
```

Run: `corepack pnpm vitest run src/components/ui/Label.test.tsx` -- expect "Cannot find module './Label'".

- [ ] **Step 2: Implement**

`Label.tsx`:
```tsx
/**
 * Label -- the one tracked-caps style in the app (spec s5 type ladder).
 * Mono 500 at 11 px, 0.08 em, uppercase. One to three words: table
 * headers, stat labels, section names. Never a sentence -- that is Meta
 * (Geist 12) or Body. Replaces Kicker.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

export interface LabelProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: "muted" | "accent" | "live" | "done" | "ink";
}

const TONE: Record<NonNullable<LabelProps["tone"]>, string> = {
  muted: "text-muted",
  accent: "text-[color:var(--color-accent-mode)]",
  live: "text-live",
  done: "text-done",
  ink: "text-ink-2",
};

const warned = new Set<string>();

export function Label({ tone = "muted", className, children, ...props }: LabelProps) {
  if (import.meta.env.DEV && typeof children === "string") {
    const words = children.trim().split(/\s+/).length;
    if (words > 3 && !warned.has(children)) {
      warned.add(children);
      console.warn(`Label: "${children}" is ${words} words; Label carries at most three words. Use Meta or Body.`);
    }
  }
  return (
    <span
      className={cn(
        "font-mono text-[11px] font-medium uppercase tracking-[0.08em] tabular-nums",
        TONE[tone],
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}
```

In `src/styles/index.css`, after `@utility tnum {...}` add:
```css
@utility numeral {
  /* The Numeral type role: every count, time and split. Never zero-padded
   * (ordinals are Label). Sizes come from the caller (text-[13px] / 20 / 26). */
  font-family: var(--font-mono);
  font-weight: 500;
  font-variant-numeric: tabular-nums lining-nums;
  font-feature-settings: "tnum" 1, "lnum" 1;
}
```

In `index.ts` add `export { Label } from "./Label";` and above the `Kicker` export a comment `// Deprecated: Kicker -> Label. Kept until the last consumer migrates.`

- [ ] **Step 3: Run tests, typecheck, commit**

Run: `corepack pnpm vitest run src/components/ui/Label.test.tsx && corepack pnpm typecheck`
```bash
git add src/splitsmith/ui_static/src/components/ui/Label.tsx src/splitsmith/ui_static/src/components/ui/Label.test.tsx src/splitsmith/ui_static/src/components/ui/index.ts src/splitsmith/ui_static/src/styles/index.css
git commit -m "feat(ui): Label primitive and numeral utility"
```

---

### Task 3: `Stat` and `StatStrip`; `StageStats` reimplemented on them

**Files:**
- Create: `src/components/ui/Stat.tsx`, `src/components/ui/Stat.test.tsx`
- Modify: `src/components/results/StageStats.tsx` (becomes a thin composition), `src/components/ui/index.ts`

**Interfaces:**
- Produces:
  ```ts
  interface StatProps { label: string; value: string; unit?: string; tone?: "ink" | "dim"; className?: string }
  function Stat(props: StatProps): JSX.Element   // label (Label) over numeral 26 px; unit 13 px muted
  interface StatStripProps { children: ReactNode; className?: string; lead?: boolean }
  function StatStrip(props): JSX.Element  // grid, shrink-0, hairline cells; `lead` gives the first cell the full row on mobile
  ```
- `StageStats` keeps its props and its existing tests (`StageStats.test.tsx` asserts `shrink-0` on the root and `col-span-2 md:col-span-1` on the stage-time cell; keep those classes on the elements the tests find).

- [ ] **Step 1: Failing tests**

`Stat.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Stat, StatStrip } from "./Stat";

describe("Stat", () => {
  it("renders label, numeral and unit", () => {
    render(<Stat label="Avg split" value="0.386" unit="s" />);
    expect(screen.getByText("Avg split").className).toMatch(/uppercase/);
    expect(screen.getByText("0.386").className).toMatch(/numeral/);
    expect(screen.getByText("s")).toBeInTheDocument();
  });
  it("dim tone marks a provisional figure", () => {
    render(<Stat label="Draw" value="1.93" tone="dim" />);
    expect(screen.getByText("1.93").className).toMatch(/text-muted/);
  });
});

describe("StatStrip", () => {
  it("cannot be crushed by a flex scroll column and leads with its first cell on mobile", () => {
    const { container } = render(
      <StatStrip lead>
        <Stat label="Stage time" value="32.09" unit="s" />
        <Stat label="Shots" value="30" />
      </StatStrip>,
    );
    const root = container.firstElementChild!;
    expect(root).toHaveClass("shrink-0");
    expect(root.firstElementChild).toHaveClass("col-span-2");
    expect(root.firstElementChild).toHaveClass("md:col-span-1");
  });
});
```

- [ ] **Step 2: Implement**

`Stat.tsx`:
```tsx
/**
 * Stat / StatStrip -- label + numeral + unit, and the hairline grid that
 * holds a row of them (spec s6). StatStrip is `shrink-0` on purpose:
 * ResultsStage mounts it first in a capped overflow-y-auto column, and an
 * overflow-hidden flex child with the default shrink collapses to its
 * label row (the #970 clip). `lead` gives the first cell the whole first
 * row below md so five tiles never leave one alone.
 */
import * as React from "react";

import { Label } from "./Label";
import { cn } from "@/lib/utils";

export interface StatProps extends React.HTMLAttributes<HTMLDivElement> {
  label: string;
  value: string;
  unit?: string;
  /** `dim` = provisional (unaudited) figure: shown, never bold. */
  tone?: "ink" | "dim";
}

export function Stat({ label, value, unit, tone = "ink", className, ...props }: StatProps) {
  return (
    <div className={cn("flex flex-col gap-1 bg-surface px-4 py-3", className)} {...props}>
      <Label>{label}</Label>
      <span className="flex items-baseline gap-1 leading-none">
        <span className={cn("numeral text-[26px]", tone === "dim" ? "text-muted" : "text-ink")}>{value}</span>
        {unit ? <span className="text-[13px] text-muted">{unit}</span> : null}
      </span>
    </div>
  );
}

export interface StatStripProps extends React.HTMLAttributes<HTMLDivElement> {
  /** First cell spans the full first row below md. */
  lead?: boolean;
  children: React.ReactNode;
}

export function StatStrip({ lead = false, className, children, ...props }: StatStripProps) {
  const cells = React.Children.toArray(children);
  return (
    <div
      className={cn(
        "grid shrink-0 grid-cols-2 gap-px overflow-hidden rounded-[10px] border border-rule-strong bg-rule-strong md:grid-flow-col md:auto-cols-fr",
        className,
      )}
      {...props}
    >
      {cells.map((cell, i) =>
        React.isValidElement<{ className?: string }>(cell)
          ? React.cloneElement(cell, {
              className: cn(cell.props.className, lead && i === 0 && "col-span-2 md:col-span-1"),
            })
          : cell,
      )}
    </div>
  );
}
```

Replace `StageStats.tsx`'s body with:
```tsx
import { Stat, StatStrip } from "@/components/ui/Stat";

interface StageStatsProps { stageTime: number | null; shotCount: number; draw: number | null; fastestSplit: number | null; avgSplit: number | null }

export function StageStats({ stageTime, shotCount, draw, fastestSplit, avgSplit }: StageStatsProps) {
  return (
    <StatStrip lead>
      <Stat label="Stage time" value={stageTime != null ? stageTime.toFixed(2) : "-"} unit={stageTime != null ? "s" : undefined} />
      <Stat label="Shots" value={String(shotCount)} />
      <Stat label="Draw" value={draw != null ? draw.toFixed(2) : "-"} unit={draw != null ? "s" : undefined} />
      <Stat label="Fastest split" value={fastestSplit != null ? fastestSplit.toFixed(3) : "-"} unit={fastestSplit != null ? "s" : undefined} />
      <Stat label="Avg split" value={avgSplit != null ? avgSplit.toFixed(3) : "-"} unit={avgSplit != null ? "s" : undefined} />
    </StatStrip>
  );
}
```
Keep the file's header comment, updated to say it composes `StatStrip`. `StageStats.test.tsx`'s existing assertions on `"1.50s"` become `"1.50"` plus a unit span; update those two expectations (`getByText("1.50")`), keep the class assertions.

- [ ] **Step 3: Tests, typecheck, commit**

Run: `corepack pnpm vitest run src/components/ui/Stat.test.tsx src/components/results && corepack pnpm typecheck`
```bash
git add src/splitsmith/ui_static/src/components/ui/Stat.tsx src/splitsmith/ui_static/src/components/ui/Stat.test.tsx src/splitsmith/ui_static/src/components/results/StageStats.tsx src/splitsmith/ui_static/src/components/results/StageStats.test.tsx src/splitsmith/ui_static/src/components/ui/index.ts
git commit -m "feat(ui): Stat and StatStrip; StageStats composes them"
```

---

### Task 4: `Chip`

**Files:**
- Create: `src/components/ui/Chip.tsx`, `src/components/ui/Chip.test.tsx`; export from `index.ts`.

**Interfaces:**
- Produces: `<Chip tick="draw" | "movement" | "transition" | "fire" | "reload" | "activation" | "muted" tone="neutral" | "warn" | "ok">children</Chip>`; a `<span>` pill, Geist 12, hairline border; the tick is a 6 px dot before the text in the interval hue (draw led, movement beep, transition manual, fire done, reload live, activation ink-2, muted muted); `warn`/`ok` tint border and text amber/green.

- [ ] **Step 1: Failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Chip } from "./Chip";

describe("Chip", () => {
  it("is a neutral pill with a coloured tick, not a coloured fill", () => {
    render(<Chip tick="movement">Movement</Chip>);
    const chip = screen.getByText("Movement");
    expect(chip.className).toMatch(/rounded-full/);
    expect(chip.className).not.toMatch(/bg-beep/);
    const tick = chip.querySelector("[data-tick]");
    expect(tick?.className).toMatch(/bg-beep/);
  });
  it("warn tone tints border and text", () => {
    render(<Chip tone="warn">4 flags</Chip>);
    expect(screen.getByText("4 flags").className).toMatch(/text-live/);
  });
});
```

- [ ] **Step 2: Implement**

```tsx
/** Chip -- neutral outline pill, Geist 12, with an optional 6 px tick that
 *  carries a hue (spec s5: one hue per meaning, in one place per row).
 *  Interval types use the tick; states use `tone`. Never a coloured fill. */
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const chip = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[12px] leading-[1.4]",
  {
    variants: {
      tone: {
        neutral: "border-rule-strong text-ink-2",
        warn: "border-live/45 text-live",
        ok: "border-done/45 text-done",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export type ChipTick = "draw" | "movement" | "transition" | "fire" | "reload" | "activation" | "muted";

const TICK: Record<ChipTick, string> = {
  draw: "bg-led",
  movement: "bg-beep",
  transition: "bg-manual",
  fire: "bg-done",
  reload: "bg-live",
  activation: "bg-ink-2",
  muted: "bg-muted",
};

export interface ChipProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof chip> {
  tick?: ChipTick;
}

export function Chip({ tick, tone, className, children, ...props }: ChipProps) {
  return (
    <span className={cn(chip({ tone }), className)} {...props}>
      {tick ? <i data-tick aria-hidden className={cn("size-1.5 shrink-0 rounded-full", TICK[tick])} /> : null}
      {children}
    </span>
  );
}
```

Note `tone="warn"` / `"ok"` render their own dot via `tick` when the caller passes one; the mockup's "4 flags" chip uses `tone="warn" tick="reload"` (amber tick).

- [ ] **Step 3: Tests, commit**

```bash
git add src/splitsmith/ui_static/src/components/ui/Chip.tsx src/splitsmith/ui_static/src/components/ui/Chip.test.tsx src/splitsmith/ui_static/src/components/ui/index.ts
git commit -m "feat(ui): Chip primitive"
```

---

### Task 5: `Button` re-skin

**Files:**
- Modify: `src/components/ui/button.tsx`
- Create: `src/components/ui/button.test.tsx`
- Modify: the seven `variant="default"` call sites (find with `grep -rn 'variant="default"' src --include=*.tsx`) -- each becomes `primary` only if it is the page's single primary action; otherwise the attribute is removed (neutral default).

**Interfaces:**
- Produces: variants `primary` (the `.btn-led-fill` recipe: led-fill, Antonio 14/700, 0.06 em, uppercase, no glow beyond the recipe's 1 px ring), `default` (surface-2, hairline `border-rule-strong`, Geist 13 medium, ink), `ghost` (transparent, ink-2, hover surface-2), `destructive` (transparent, `border-led/45`, `text-led-text`; never a red fill), `outline` and `secondary` become aliases of `default`, `link` unchanged. Sizes unchanged. `[&_svg]:size-4` kept.

- [ ] **Step 1: Failing tests**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button } from "./button";

describe("Button budget", () => {
  it("primary is the Antonio led-fill recipe", () => {
    render(<Button variant="primary">Save</Button>);
    expect(screen.getByRole("button").className).toMatch(/btn-led-fill/);
  });
  it("default is neutral Geist, not red", () => {
    render(<Button>Compare</Button>);
    const c = screen.getByRole("button").className;
    expect(c).not.toMatch(/bg-led/);
    expect(c).toMatch(/border-rule-strong/);
  });
  it("destructive is an outline, never a fill", () => {
    render(<Button variant="destructive">Remove</Button>);
    const c = screen.getByRole("button").className;
    expect(c).not.toMatch(/bg-destructive/);
    expect(c).toMatch(/text-led-text/);
  });
});
```

- [ ] **Step 2: Implement**

Replace `buttonVariants`:
```ts
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-[13px] font-medium leading-[1.2] transition-colors duration-150 ease-[var(--ease-default)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // One per view: the page's single primary action (spec s5).
        primary: "btn-led-fill rounded-lg",
        default: "border border-rule-strong bg-surface-2 text-ink hover:bg-surface-3",
        secondary: "border border-rule-strong bg-surface-2 text-ink hover:bg-surface-3",
        outline: "border border-rule-strong bg-surface-2 text-ink hover:bg-surface-3",
        ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
        // Danger is an outline with led-text -- never a red fill.
        destructive: "border border-led/45 bg-transparent text-led-text hover:bg-led-tint",
        link: "text-led-text underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-3.5",
        sm: "h-8 rounded-md px-3 text-[12px]",
        lg: "h-10 px-5",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);
```

`.btn-led-fill` in `index.css` sets `font-size: 0.875rem` and Antonio; it already exists. Add `.btn-led-fill { border-radius: inherit; }` is not needed (`rounded-lg` is on the class list).

- [ ] **Step 3: Audit the explicit `variant="default"` sites**

For each of the seven: open the page, decide whether it is that view's single primary action. Promote to `variant="primary"` only: the Pick page's "New match", the Audit page's "Save & next", Export's "Export bundle", CreateMatch's create/submit, Account's "Save" (the display-name form's one action). Everything else drops the attribute. Record the list in the commit body.

- [ ] **Step 4: Run every SPA test, typecheck, lint; commit**

Run: `corepack pnpm test && corepack pnpm typecheck && corepack pnpm lint`
Some tests match buttons by class or by `bg-led`; fix the expectation only where the test pins the old red default (it is asserting the pair the spec retires).

```bash
git add src/splitsmith/ui_static/src/components/ui/button.tsx src/splitsmith/ui_static/src/components/ui/button.test.tsx <the call-site files>
git commit -m "feat(ui): Button budget -- primary is the led-fill recipe, default is neutral, destructive is an outline"
```

---

### Task 6: `PageHeader`

**Files:**
- Create: `src/components/ui/PageHeader.tsx`, `src/components/ui/PageHeader.test.tsx`; export.

**Interfaces:**
- Produces:
  ```ts
  interface PageHeaderProps { ordinal?: string; title: string; sub?: ReactNode; back?: { label: string; to: string }; actions?: ReactNode; children?: ReactNode /* e.g. the shooter chip strip, rendered under the sub-line */ }
  ```
  Renders `<header>` with an `<h1>` in Antonio 700 30 px uppercase (`font-display text-[30px] font-bold uppercase leading-none tracking-[0.01em] text-ink`), the ordinal as `<span class="text-led mr-2">`, the sub-line Geist 13 muted, `back` as a `<Link>` in Meta above the title, `actions` right-aligned and bottom-aligned with the title.

- [ ] **Step 1: Failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("renders ordinal, title, sub-line, back link and actions", () => {
    render(
      <MemoryRouter>
        <PageHeader ordinal="03" title="B6 Rear" sub="Mathias Axell" back={{ label: "All stages", to: "/results" }} actions={<button>Share</button>} />
      </MemoryRouter>,
    );
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent("03B6 Rear");
    expect(h1.className).toMatch(/font-display/);
    expect(screen.getByText("03").className).toMatch(/text-led/);
    expect(screen.getByRole("link", { name: /all stages/i })).toHaveAttribute("href", "/results");
    expect(screen.getByRole("button", { name: "Share" })).toBeInTheDocument();
    expect(screen.getByText("Mathias Axell")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Implement**

```tsx
/** PageHeader -- the only way to get a page or stage title (spec s6).
 *  Antonio 700 30 px, ordinal in led, sub-line Geist 13 muted, actions
 *  bottom-aligned on the right. `children` renders under the sub-line
 *  (the shooter chip strip on multi-shooter matches). */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { cn } from "@/lib/utils";

export interface PageHeaderProps {
  ordinal?: string;
  title: string;
  sub?: ReactNode;
  back?: { label: string; to: string };
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
}

export function PageHeader({ ordinal, title, sub, back, actions, children, className }: PageHeaderProps) {
  return (
    <header className={cn("mb-5 flex flex-wrap items-end justify-between gap-x-4 gap-y-3", className)}>
      <div className="min-w-0">
        {back ? (
          <Link to={back.to} className="mb-1.5 inline-block text-[12px] text-muted hover:text-ink">
            &lsaquo; {back.label}
          </Link>
        ) : null}
        <h1 className="font-display text-[30px] font-bold uppercase leading-none tracking-[0.01em] text-ink">
          {ordinal ? <span className="mr-2 text-led">{ordinal}</span> : null}
          {title}
        </h1>
        {sub ? <p className="mt-1.5 text-[13px] text-muted">{sub}</p> : null}
        {children ? <div className="mt-3">{children}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}
```

- [ ] **Step 3: Test, commit**

```bash
git add src/splitsmith/ui_static/src/components/ui/PageHeader.tsx src/splitsmith/ui_static/src/components/ui/PageHeader.test.tsx src/splitsmith/ui_static/src/components/ui/index.ts
git commit -m "feat(ui): PageHeader primitive"
```

---

### Task 7: `DataTable` row primitives and `PipelineDots`

**Files:**
- Create: `src/components/ui/DataTable.tsx`, `src/components/ui/DataTable.test.tsx`, `src/components/ui/PipelineDots.tsx`, `src/components/ui/PipelineDots.test.tsx`; export both.

**Interfaces:**
- Produces:
  ```ts
  function Table(props: TableHTMLAttributes)            // wrapper: rounded-[10px] border border-rule bg-surface overflow-hidden; <table class="w-full border-collapse text-[13px]">
  function Th(props & { align?: "left" | "right" })     // Label style, py-2 px-3, border-b border-rule-strong
  function Td(props & { kind?: "text" | "num" | "ordinal" | "name"; dim?: boolean })
     // num: numeral text-ink text-right; ordinal: font-mono text-[11px] text-muted w-7; name: text-ink font-medium; dim: text-subtle
  function Tr(props & { current?: boolean })            // border-b border-rule; current: bg-surface-2 with a 2 px led inset on the first cell (via [&>td:first-child]:shadow-[inset_2px_0_0_var(--color-led)])
  type PipelineState = "todo" | "progress" | "done"
  function PipelineDots({ states, label }: { states: PipelineState[]; label: string })   // role="img" aria-label; 8 px dots: done green fill, progress amber fill, todo hollow rule-strong
  ```

- [ ] **Step 1: Failing tests**

`DataTable.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Table, Td, Th, Tr } from "./DataTable";

describe("DataTable", () => {
  it("numeric cells are right-aligned numerals; current row carries the led inset", () => {
    render(
      <Table>
        <thead><tr><Th>Stage</Th><Th align="right">Draw</Th></tr></thead>
        <tbody>
          <Tr current><Td kind="ordinal">03</Td><Td kind="num">1.97</Td></Tr>
          <Tr><Td kind="name" dim>B5 All</Td><Td kind="num" dim>1.93</Td></Tr>
        </tbody>
      </Table>,
    );
    expect(screen.getByText("Draw").className).toMatch(/text-right/);
    expect(screen.getByText("1.97").className).toMatch(/numeral/);
    expect(screen.getByText("1.97").className).toMatch(/text-right/);
    expect(screen.getByText("03").closest("tr")!.className).toMatch(/bg-surface-2/);
    expect(screen.getByText("1.93").className).toMatch(/text-subtle/);
  });
});
```

`PipelineDots.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PipelineDots } from "./PipelineDots";

describe("PipelineDots", () => {
  it("is one labelled image with one dot per stage and no colour-only meaning", () => {
    render(<PipelineDots states={["done", "progress", "todo"]} label="1 of 3 stages audited" />);
    const img = screen.getByRole("img", { name: "1 of 3 stages audited" });
    const dots = img.querySelectorAll("[data-state]");
    expect(dots).toHaveLength(3);
    expect(dots[0]).toHaveAttribute("data-state", "done");
    expect(dots[2].className).toMatch(/border/);
  });
});
```

- [ ] **Step 2: Implement**

`DataTable.tsx`:
```tsx
/** DataTable row primitives (spec s6). Lists are rows with hairline
 *  dividers, never stacked cards. Th is Label; numeric Td is Numeral;
 *  the current row is surface-2 with a 2 px led inset on its first cell. */
import * as React from "react";

import { cn } from "@/lib/utils";

export function Table({ className, children, ...props }: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      <div className="overflow-x-auto">
        <table className={cn("w-full border-collapse text-[13px]", className)} {...props}>{children}</table>
      </div>
    </div>
  );
}

export function Th({ align = "left", className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement> & { align?: "left" | "right" }) {
  return (
    <th
      className={cn(
        "border-b border-rule-strong px-3 py-2 font-mono text-[11px] font-medium uppercase tracking-[0.08em] text-muted",
        align === "right" ? "text-right" : "text-left",
        className,
      )}
      {...props}
    />
  );
}

export interface TdProps extends React.TdHTMLAttributes<HTMLTableCellElement> {
  kind?: "text" | "num" | "ordinal" | "name";
  dim?: boolean;
}

const KIND: Record<NonNullable<TdProps["kind"]>, string> = {
  text: "text-ink-2",
  num: "numeral text-right text-ink",
  ordinal: "w-7 font-mono text-[11px] text-muted",
  name: "font-medium text-ink",
};

export function Td({ kind = "text", dim = false, className, ...props }: TdProps) {
  return <td className={cn("px-3 py-2 align-middle", KIND[kind], dim && "text-subtle", className)} {...props} />;
}

export function Tr({ current = false, className, ...props }: React.HTMLAttributes<HTMLTableRowElement> & { current?: boolean }) {
  return (
    <tr
      className={cn(
        "border-b border-rule last:border-b-0",
        current && "bg-surface-2 [&>td:first-child]:shadow-[inset_2px_0_0_var(--color-led)]",
        className,
      )}
      {...props}
    />
  );
}
```

`PipelineDots.tsx`:
```tsx
/** PipelineDots -- a stage's progress as a row of 8 px dots (spec s6).
 *  done = green fill, progress = amber fill, todo = hollow. Every dot
 *  also carries data-state so the meaning survives greyscale. Replaces
 *  TickStrip's check glyphs on Matches, Overview rows and the sidebar. */
import { cn } from "@/lib/utils";

export type PipelineState = "todo" | "progress" | "done";

const DOT: Record<PipelineState, string> = {
  done: "bg-done",
  progress: "bg-live",
  todo: "border-[1.5px] border-rule-strong bg-transparent",
};

export function PipelineDots({ states, label, className }: { states: PipelineState[]; label: string; className?: string }) {
  return (
    <span role="img" aria-label={label} className={cn("inline-flex items-center gap-1", className)}>
      {states.map((s, i) => (
        <i key={i} data-state={s} aria-hidden className={cn("inline-block size-2 rounded-full", DOT[s])} />
      ))}
    </span>
  );
}
```

- [ ] **Step 3: Tests, commit**

```bash
git add src/splitsmith/ui_static/src/components/ui/DataTable.tsx src/splitsmith/ui_static/src/components/ui/DataTable.test.tsx src/splitsmith/ui_static/src/components/ui/PipelineDots.tsx src/splitsmith/ui_static/src/components/ui/PipelineDots.test.tsx src/splitsmith/ui_static/src/components/ui/index.ts
git commit -m "feat(ui): DataTable row primitives and PipelineDots"
```

---

### Task 8: `ProgressStrip` and the strip slot

**Files:**
- Create: `src/components/ui/ProgressStrip.tsx`, `src/components/ui/ProgressStrip.test.tsx`
- Modify: `src/components/layout/shellChromeContext.tsx` (add `stripSlot`, `useShellStripSlot`), `src/components/layout/RootLayout.tsx` (render the slot under the GlobalBar), `src/components/Jobs.tsx` (`JobsSurface` portals the strip), `src/components/layout/globalChrome.test.tsx` (extend), `src/components/layout/RootLayout.test.tsx` (extend).

**Interfaces:**
- Produces:
  ```ts
  interface ProgressStripProps { state: JobsState; onOpen: () => void; onDismissFailed: (job: Job) => void }
  function ProgressStrip(props): JSX.Element | null
  // null when no running/pending and no unacknowledged failed job.
  // Running: amber dot (the one glow), "<KIND_LABEL[kind]> · stage NN <name?>", bar from job.progress (indeterminate shimmer when null), "<done> of <total>", "All jobs" button.
  // Failed (and no running): red dot, "<label> failed · stage NN", "<n> min ago", Retry / Details / Dismiss.
  ```
  `ShellChromeValue.stripSlot: HTMLElement | null`, `useShellStripSlot(): HTMLElement | null`.

- [ ] **Step 1: Failing tests**

`ProgressStrip.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Job } from "@/lib/api";
import type { JobsState } from "@/lib/jobs";

import { ProgressStrip } from "./ProgressStrip";

function job(over: Partial<Job>): Job {
  return { id: "j1", kind: "shot_detect", match_id: "m", stage_number: 9, shooter_slug: "s", video_id: null, status: "running", progress: 0.6, message: null, error: null, cancel_requested: false, acknowledged: false, ...over } as Job;
}
function state(jobs: Job[]): JobsState {
  const running = jobs.filter((j) => j.status === "running");
  const pending = jobs.filter((j) => j.status === "pending");
  const failed = jobs.filter((j) => j.status === "failed" && !j.acknowledged);
  return { jobs, running, pending, failed, error: null, refresh: vi.fn(), acknowledge: vi.fn(), acknowledgeAll: vi.fn(), cancel: vi.fn(), retry: vi.fn() };
}

describe("ProgressStrip", () => {
  it("renders nothing when idle", () => {
    const { container } = render(<ProgressStrip state={state([])} onOpen={() => {}} onDismissFailed={() => {}} />);
    expect(container.firstChild).toBeNull();
  });
  it("names the running job and stage, shows progress and the count", () => {
    render(<ProgressStrip state={state([job({}), job({ id: "j2", status: "pending", stage_number: 10 })])} onOpen={() => {}} onDismissFailed={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent(/detect shots.*stage 9/i);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "60");
    expect(screen.getByText(/1 of 2/)).toBeInTheDocument();
  });
  it("shows a failed job until dismissed", () => {
    const dismiss = vi.fn();
    const failed = job({ status: "failed", error: "boom", progress: null });
    render(<ProgressStrip state={state([failed])} onOpen={() => {}} onDismissFailed={dismiss} />);
    expect(screen.getByRole("status")).toHaveTextContent(/failed/i);
    screen.getByRole("button", { name: /dismiss/i }).click();
    expect(dismiss).toHaveBeenCalledWith(failed);
  });
});
```

Check `JobStatus` in `lib/api.ts` for the exact status strings (`"failed"` is expected; adjust if the union differs) and `KIND_LABEL` in `components/Jobs.tsx` for the label of `shot_detect` ("Detect shots").

- [ ] **Step 2: Implement `ProgressStrip`**

```tsx
/** ProgressStrip -- the ambient jobs line under the top bar (spec s3.2).
 *  Rendered by JobsSurface (which owns the jobs state and the drawer)
 *  and portalled into RootLayout's strip slot, so there is exactly one
 *  poller per shell and the strip's click opens the same drawer.
 *  Nothing renders while idle. The amber dot is the page's one glow. */
import { KIND_LABEL } from "@/components/Jobs";
import type { Job } from "@/lib/api";
import type { JobsState } from "@/lib/jobs";
import { cn } from "@/lib/utils";

export interface ProgressStripProps {
  state: JobsState;
  onOpen: () => void;
  onDismissFailed: (job: Job) => void;
}

function stageText(job: Job): string {
  return job.stage_number != null ? ` · stage ${job.stage_number}` : "";
}

export function ProgressStrip({ state, onOpen, onDismissFailed }: ProgressStripProps) {
  const active = [...state.running, ...state.pending];
  const failed = state.failed[0];
  if (active.length === 0 && !failed) return null;
  const base = "flex h-7 items-center gap-3 border-b border-rule bg-[#0d0f12] px-4 text-[12px] text-muted";
  if (active.length > 0) {
    const cur = state.running[0] ?? active[0];
    const done = state.jobs.filter((j) => j.status === "succeeded").length;
    const total = done + active.length;
    const pct = cur.progress != null ? Math.round(cur.progress * 100) : null;
    return (
      <div role="status" className={base}>
        <i aria-hidden className="size-1.5 rounded-full bg-live shadow-[0_0_8px_rgba(251,191,36,0.5)]" />
        <span className="text-ink-2">{KIND_LABEL[cur.kind] ?? cur.kind}{stageText(cur)}</span>
        <span role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined} className="relative h-[3px] w-40 overflow-hidden rounded bg-surface-3">
          <span className={cn("absolute inset-y-0 left-0 rounded bg-live", pct == null && "w-1/3 motion-safe:animate-pulse")} style={pct != null ? { width: `${pct}%` } : undefined} />
        </span>
        <span className="numeral">{done + 1} of {total}</span>
        <button type="button" onClick={onOpen} className="ml-auto text-muted hover:text-ink">All jobs &rsaquo;</button>
      </div>
    );
  }
  return (
    <div role="status" className={base}>
      <i aria-hidden className="size-1.5 rounded-full bg-led" />
      <span className="text-led-text">{KIND_LABEL[failed!.kind] ?? failed!.kind} failed{stageText(failed!)}</span>
      <span className="ml-auto flex gap-3">
        <button type="button" onClick={() => void state.retry(failed!)} className="hover:text-ink">Retry</button>
        <button type="button" onClick={onOpen} className="hover:text-ink">Details</button>
        <button type="button" onClick={() => onDismissFailed(failed!)} className="hover:text-ink">Dismiss</button>
      </span>
    </div>
  );
}
```

If `KIND_LABEL` importing from `components/Jobs.tsx` creates a cycle (Jobs imports ProgressStrip), move `KIND_LABEL` and `KIND_ICON` into `src/lib/jobLabels.ts` and import from there in both files.

- [ ] **Step 3: The slot**

`shellChromeContext.tsx`: add `stripSlot: HTMLElement | null` to `ShellChromeValue` and
```ts
export function useShellStripSlot(): HTMLElement | null {
  return useContext(ShellChromeContext)?.stripSlot ?? null;
}
```
`RootLayout.tsx`: `const [stripSlot, setStripSlot] = useState<HTMLElement | null>(null);`, include it in `value`, and render `<div ref={setStripSlot} />` between `<GlobalBar />` and the context slot. `useShellHeaderHeight` already uses a ResizeObserver, so the strip appearing re-measures `--shell-header-h`.

`Jobs.tsx` `JobsSurface`: `const stripSlot = useShellStripSlot();` and render
```tsx
{stripSlot ? createPortal(<ProgressStrip state={state} onOpen={() => setOpen(true)} onDismissFailed={(j) => void state.acknowledge(j)} />, stripSlot) : null}
```
Remove the JobsRail's glow-on-running treatment if it has one (grep `shadow-\[0_0` in the rail) -- the strip is the one glow.

- [ ] **Step 4: Extend the chrome tests**

In `globalChrome.test.tsx` add a test that mounts `RootLayout` with a route whose element calls `useShellStripSlot()` and portals a `<div data-testid="strip" />` into it, and asserts the strip renders inside the `<header>` (the test file already has a mount helper for the layout; reuse it). In `RootLayout.test.tsx` assert the slot exists between the global bar and the context slot (`header.children[1]` is the strip slot when the bar renders).

- [ ] **Step 5: Tests, typecheck, commit**

Run: `corepack pnpm test && corepack pnpm typecheck`
```bash
git add src/splitsmith/ui_static/src/components/ui/ProgressStrip.tsx src/splitsmith/ui_static/src/components/ui/ProgressStrip.test.tsx src/splitsmith/ui_static/src/components/layout src/splitsmith/ui_static/src/components/Jobs.tsx src/splitsmith/ui_static/src/lib/jobLabels.ts src/splitsmith/ui_static/src/components/ui/index.ts
git commit -m "feat(ui): ProgressStrip under the top bar, fed by each shell's JobsSurface"
```

---

### Task 9: Breadcrumb into the top bar; context row becomes shooter-strip only

**Files:**
- Modify: `src/components/layout/shellChromeContext.tsx` (`crumbSlot`, `useShellCrumbSlot`), `src/components/layout/GlobalBar.tsx` (render the crumb slot after the brand), `src/components/layout/RootLayout.tsx` (state + provider value), `src/components/match/MatchShell.tsx:552-620` (portal a Geist breadcrumb into the crumb slot; the context row renders only the shooter strip and only when `shooters.length > 1`; remove the Switch project button), tests: `MatchShell.test.tsx`, `GlobalBar.test.tsx`.

**Interfaces:**
- Produces: `useShellCrumbSlot(): HTMLElement | null`. Breadcrumb markup: `<nav aria-label="Breadcrumb" class="flex min-w-0 items-center gap-2 text-[13px]">` with `Matches` as a `<button>` (navigate to `/pick`, replace) in `text-muted hover:text-ink`, a `/` in `text-rule-strong`, the match name in `text-ink font-medium truncate`, and the view label in `text-ink-2`. No Antonio, no uppercase. On mobile `GlobalBar` is hidden when the shell owns the mobile account (unchanged), so the crumb slot only exists on desktop; `MatchShell` portals only when the slot is non-null.

- [ ] **Step 1: Failing tests**

In `GlobalBar.test.tsx` add: rendering `GlobalBar` inside a `ShellChromeProvider` whose `crumbSlot` is captured by a ref exposes an element with `data-testid="crumb-slot"` between the brand text and the mode switch.

In `MatchShell.test.tsx` add: with a bound match, the document contains `nav[aria-label="Breadcrumb"]` whose text is `Matches / <name>` (and `/ Audit` on an audit route), it is not inside the context slot, and there is no button named "Switch project". Read the file's existing render helper (it mocks `api.getHealth`, `getProject`, `listMatchShooters`) and reuse it.

- [ ] **Step 2: Implement**

`shellChromeContext.tsx`: add `crumbSlot: HTMLElement | null` and `useShellCrumbSlot()`.

`RootLayout.tsx`: `const [crumbSlot, setCrumbSlot] = useState<HTMLElement | null>(null);` in the provider value; pass `setCrumbSlot` to `GlobalBar` via a prop `onCrumbSlot={setCrumbSlot}`.

`GlobalBar.tsx`: after the "Splitsmith" wordmark (desktop only) render `<div ref={onCrumbSlot} data-testid="crumb-slot" className="ml-3 flex min-w-0 flex-1 items-center" />` and drop the separate `<div className="flex-1" />` (the slot is the spacer now).

`MatchShell.tsx`: build
```tsx
const crumb = (
  <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-2 text-[13px]">
    <button type="button" onClick={switchProject} className="text-muted transition-colors hover:text-ink">Matches</button>
    <span aria-hidden className="text-rule-strong">/</span>
    <span className="truncate font-medium text-ink">{project?.name ?? health?.project_name ?? "..."}</span>
    {viewLabel ? (<><span aria-hidden className="text-rule-strong">/</span><span className="text-ink-2">{viewLabel}</span></>) : null}
  </nav>
);
```
and `{crumbSlot && !isMobile ? createPortal(crumb, crumbSlot) : null}`. The desktop `contextRow` becomes: when `shooters.length > 1`, `<div className="flex items-center gap-4 border-t border-rule bg-bg px-7 py-2"><ShooterChipStrip ... /></div>`; otherwise `null` (so the slot is empty and zero height). Delete the Switch project button and `userInitials` usage if it becomes unused (keep `switchProject`, it backs the Matches crumb). The mobile row (hamburger + brand + name) is unchanged.

The `viewLabel` map in `MatchShell.tsx:81-100` uses the nav labels; Task 10 renames two of them -- update both places in that task.

- [ ] **Step 3: Tests, typecheck, commit**

```bash
git add src/splitsmith/ui_static/src/components/layout src/splitsmith/ui_static/src/components/match/MatchShell.tsx src/splitsmith/ui_static/src/components/match/MatchShell.test.tsx
git commit -m "feat(shell): breadcrumb in the top bar; context row carries only the shooter strip

The 60 px context row is gone on single-shooter matches (the slot is
empty and collapses). Matches in the breadcrumb is the switch-project
action. The shooter chip strip stays in the slot until PRs 3-6 move it
under each page title."
```

---

### Task 10: Grouped nav, renames, Jobs row removal, sidebar restyle, StageDot tiers

**Files:**
- Modify: `src/components/match/navItems.tsx` (add `group`, rename labels, drop the `jobs` item), `src/components/match/navItems.test.ts`, `src/components/match/MatchSidebar.tsx` (group headers; match head; stage rows; muted "next"), `src/components/match/MatchShell.tsx` (view labels "Splits"/"Footage"; `jobsAttentionCount` no longer passed to nav), `src/components/ui/StageDot.tsx` (ready tier loses the red halo), `src/components/match/MobileNav.tsx` (same items; group headers optional).

**Interfaces:**
- Produces: `MatchNavItem.group?: "prepare" | "review" | "analyse" | "deliver"` (undefined = ungrouped, i.e. Overview). Labels: `overview` "Overview"; `videos` "Footage" (group prepare); `shooters` "Shooters" (prepare); `audit` "Audit" (review); `beep-review` (review); `triage` (review); `results` "Splits" (analyse); `coach` "Coach" (analyse); `export` "Export" (deliver). Item order: overview, videos, shooters, audit, beep-review, triage, results, coach, export. No `jobs` item; `matchNavItems` drops the `jobsAttentionCount` argument.

- [ ] **Step 1: Failing tests**

Replace `navItems.test.ts`'s jobs test with:
```ts
import { describe, expect, it } from "vitest";
import { matchNavItems } from "./navItems";

const base = { base: "/match/m", shooterSlug: "s", hasFootage: true, shooterCount: 1, beepReviewPendingCount: 0, triageFlaggedCount: 0 };

describe("matchNavItems", () => {
  it("has no jobs row; the progress strip and drawer own jobs", () => {
    expect(matchNavItems(base).find((i) => i.key === "jobs")).toBeUndefined();
  });
  it("groups rows by phase in loop order and carries the approved labels", () => {
    const items = matchNavItems(base);
    expect(items.map((i) => [i.key, i.group ?? null, i.label])).toEqual([
      ["overview", null, "Overview"],
      ["videos", "prepare", "Footage"],
      ["shooters", "prepare", "Shooters"],
      ["audit", "review", "Audit"],
      ["beep-review", "review", "Beep review"],
      ["triage", "review", "Triage"],
      ["results", "analyse", "Splits"],
      ["coach", "analyse", "Coach"],
      ["export", "deliver", "Export"],
    ]);
  });
});
```
Keep any other existing tests in that file that still hold (footage hint, shooter-slug routing).

- [ ] **Step 2: Implement `navItems.tsx`**

Add `group?: "prepare" | "review" | "analyse" | "deliver";` to `MatchNavItem`; remove `jobsAttentionCount` from the args type and destructuring; reorder the array to the order above; set `group` on each; change `label: "Results"` to `"Splits"` and `label: "Videos"` to `"Footage"`; delete the `jobs` entry and the now-unused `Activity` icon import.

- [ ] **Step 3: Sidebar group headers and restyle**

In `MatchSidebar.tsx` replace the `.map((item) => <SidebarLink .../>)` with a loop that emits a header when `item.group` changes:
```tsx
{(() => {
  const items = matchNavItems({ base, shooterSlug, hasFootage, shooterCount, beepReviewPendingCount: beepReviewPendingCount ?? 0, triageFlaggedCount: triageFlaggedCount ?? 0, footageHint });
  let lastGroup: string | undefined;
  return items.flatMap((item) => {
    const nodes: ReactNode[] = [];
    if (item.group && item.group !== lastGroup && !collapsed) {
      nodes.push(<Label key={`g-${item.group}`} className="mt-2 px-2.5 pb-0.5 text-subtle">{GROUP_LABEL[item.group]}</Label>);
    }
    lastGroup = item.group;
    nodes.push(<SidebarLink key={item.key} ...same props as before... >{item.label}</SidebarLink>);
    return nodes;
  });
})()}
```
with `const GROUP_LABEL = { prepare: "Prepare", review: "Review", analyse: "Analyse", deliver: "Deliver" } as const;`. Remove the `jobsAttentionCount` prop from `MatchSidebarProps` and its call site in `MatchShell.tsx`.

`SidebarLink` active state: replace `"border border-led-deep bg-[color:var(--color-led-tint)] px-[9px] font-bold text-led"` with `"bg-surface-3 font-medium text-ink shadow-[inset_2px_0_0_var(--color-led)]"` and the inactive `"border border-transparent text-ink-2 hover:bg-surface-2 hover:text-ink"` with `"text-ink-2 hover:bg-surface-2 hover:text-ink"`; the icon's active colour becomes `text-ink`. Badge counts: `{pad2(count!)}` becomes `{count}` (no leading zeros on counts).

Match head: the kicker line (`matchKicker`, "ACTIVE MATCH") is removed; the name keeps `font-display` (allowed: it is the sidebar's one Antonio use) at `text-[17px]`; the subtitle becomes `<Label>` with the date.

Stage rows: ordinal badge becomes plain `<span className="w-[18px] font-mono text-[11px] text-muted">{pad2(stage.stage_number)}</span>` (ordinals keep the zero); active row `"bg-surface-3 text-ink shadow-[inset_2px_0_0_var(--color-led)]"`, inactive `"text-ink-2 hover:bg-surface-2"`; the "next" tag becomes `<Label className="text-subtle">next</Label>`; remove the `isNextUp` badge colouring (`border-led-deep bg-led-tint text-led-text`). The "STAGES  04 / 12" header shows `{audited} / {total}` unpadded.

`StageDot.tsx` `ready` case: replace the led halo and led border with a hollow dot `"size-2 rounded-full border-[1.5px] border-rule-strong bg-transparent"` (aria-label unchanged: "Ready to audit"); `in_progress` keeps the amber fill and pulse; `audited` keeps green + check; `todo` hollow; `partial` dashed amber; `skipped` bar. Update `StageDot`'s header comment table.

- [ ] **Step 4: View labels**

In `MatchShell.tsx` the `viewLabel` derivation (around lines 81-100): "Results" -> "Splits", "Videos" -> "Footage". Search the SPA tests for `"Results"` / `"Videos"` label expectations (`grep -rn "Results\b\|Videos\b" src --include=*.test.tsx`) and update those that pin the nav or breadcrumb label (not page titles, which PRs 4 and 6 change).

- [ ] **Step 5: Tests, typecheck, lint; commit**

Run: `corepack pnpm test && corepack pnpm typecheck && corepack pnpm lint`
```bash
git add src/splitsmith/ui_static/src/components/match src/splitsmith/ui_static/src/components/ui/StageDot.tsx
git commit -m "feat(shell): nav grouped by phase, Results->Splits and Videos->Footage, Jobs row removed, sidebar in the budget

Red is for the current position only: the active row is an inset bar, the
next-up stage is a muted label, and the ready tier of StageDot is a hollow
dot instead of a red halo. Counts drop their leading zeros; stage ordinals
keep theirs."
```

---

### Task 11: Design page entries

**Files:**
- Modify: `src/pages/Design.tsx` -- add a "Budget primitives" section at the top rendering every new primitive in every variant (`Label` tones, `Stat` ink/dim, `StatStrip lead` with five cells, `Table` with a current and a dim row, `Chip` with every tick and tone, `Button` four variants and three sizes, `PageHeader` with ordinal/back/actions, `PipelineDots`, `ProgressStrip` running and failed built from a static `JobsState`). Delete this file's grandfather comment and make it lint-clean using only primitives (it is the reference page).

- [ ] **Step 1: Add the section, run lint on the file**

Run: `corepack pnpm exec eslint src/pages/Design.tsx` -- expect 0 errors after removing the disable comment.

- [ ] **Step 2: Look at it once**

Start the local server against the demo match (Global Constraints), open `http://127.0.0.1:5174/design`, screenshot at 1440. Compare against the mockup's primitives sheet; fix only what differs.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Design.tsx
git commit -m "docs(ui): Design page shows the budget primitives"
```

---

### Task 12: Whole-branch verification and PR

- [ ] **Step 1: Gates**

```bash
cd src/splitsmith/ui_static && corepack pnpm typecheck && corepack pnpm lint && corepack pnpm test && corepack pnpm build
cd /home/mathias/work/splitsmith && uv run pytest tests/test_ui_server.py -q -k "spa or static or index" ; git checkout uv.lock
```
(the Python suite is unaffected by SPA-only changes; the `-k` subset covers the routes that serve `dist/`.)

- [ ] **Step 2: Screenshots against the demo match**

Seed and start the server (Global Constraints). At 1440x900 screenshot: `/pick`, the match Overview, `/results`, `/results/s_demo0001/3`, `/audit/s_demo0001/3`, `/design`; at 390x844: the Overview and the stage page. Check against the mockup: one bar, no context row on this single-shooter match, grouped nav with Footage / Splits, no Jobs row, muted "next", hollow ready dots, neutral default buttons with at most one red primary per page, and the strip absent while idle. To see the strip, trigger a job from the Audit page's gate (`Run` on a stage without shots -- stage 9) and screenshot while it runs; it will fail (no ffmpeg) and show the failed state, which is also wanted. Stop the server; delete `.playwright-mcp/`.

- [ ] **Step 3: Republish the mockup artifact with the screenshots**

Add a "Built" section to `splitsmith-shell-primitives.html` (scratchpad) with the six desktop screenshots as base64 JPEG (downscale to 1000 px, mask nothing -- the demo match has no video frames) and republish to the same URL so the user reviews the real thing before merge.

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin ux/pr2-shell-primitives
gh pr create --base main --title "feat(ui): one-bar shell, progress strip, grouped nav and the budget primitives" --body-file - <<'EOF'
PR 2 of the UX restructure (spec: docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md, sections 3.3, 5, 6). Mockup approved 2026-09-14: https://claude.ai/code/artifact/2437d7ec-c7dc-444b-a747-1a0823c7d301

Shell: breadcrumb moves into the top bar (Matches = switch project); the context row now carries only the shooter chip strip and collapses on single-shooter matches; a ProgressStrip renders under the bar while jobs run, portalled from each shell's JobsSurface so there is still one poller; nav grouped by phase with Results -> Splits and Videos -> Footage; the Jobs row is gone (strip + drawer own it; /jobs stays routable until PR 8).

Primitives in components/ui: Label, Stat/StatStrip (StageStats now composes it), Chip, Button (primary = led-fill recipe, default neutral, destructive outline), PageHeader, DataTable rows, PipelineDots, ProgressStrip, numeral utility. Old primitives stay exported and are marked deprecated.

Lint: no-restricted-syntax rejects arbitrary text sizes, trackings, font-display and bg-led/text-bg outside components/ui; ~75 files grandfathered with a file-level disable that each rebuild PR removes.

No page is rebuilt. Screenshots against a seeded local demo match are in the artifact's "Built" section.

Squash-merge; single-paragraph squash body.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01Est8bRLwpxCKqPXsopFKQz
EOF
```

---

## Self-review

- Spec coverage: s3.3 (one bar, strip slot on RootLayout, extend `globalChrome.test.tsx`) -> Tasks 8, 9; s5 rules -> enforced by Task 1 and embodied in Tasks 2-7; s6 primitives -> Tasks 2-8 (TimeBudgetBar deferred to PR 7 per the approved mockup; `DisplayHeading`/`Kicker`/`Readout` kept as deprecated exports rather than folded, since 18 files still consume them and this PR rebuilds no page); s3.2 renames and Jobs row -> Task 10; approved "muted next" -> Task 10.
- Placeholders: none. Task 5 step 3 names the five promotion sites explicitly; Task 9 tells the executor which helper to reuse.
- Type consistency: `useShellStripSlot` / `useShellCrumbSlot` defined in Task 8 / Task 9 and consumed in the same tasks; `ProgressStripProps.onDismissFailed(job)` matches `JobsSurface`'s `state.acknowledge(j)`; `PipelineState` values ("todo" | "progress" | "done") are what Task 10's sidebar does not use (the sidebar keeps `StageDot`); `Chip`'s `tick` union matches the interval classes plus `muted`.
