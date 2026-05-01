/**
 * /_design — the visual spec for the production UI.
 *
 * Per issue #12: "if a component renders correctly here, it renders correctly
 * in the app." Render every token, every shadcn component, every custom
 * component on a single scrollable page so visual QA is one page reload away.
 */

import { Check, Crosshair, Eye, Plus, Save, Trash2 } from "lucide-react";

import { MarkerGlyph } from "@/components/MarkerGlyph";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useTheme } from "@/lib/theme";

const SHADCN_TOKENS = [
  "background",
  "foreground",
  "card",
  "popover",
  "primary",
  "secondary",
  "muted",
  "accent",
  "destructive",
  "border",
  "input",
  "ring",
];

const SPLIT_TOKENS = [
  {
    name: "split-good",
    label: "Split ≤ 0.25s",
    fg: "split-good-foreground",
    note: "Okabe-Ito bluish green · 3.4:1 on white (AA UI)",
  },
  {
    name: "split-ok",
    label: "Split 0.25–0.35s",
    fg: "split-ok-foreground",
    note: "Okabe-Ito yellow · paired with dark fg for AA text",
  },
  {
    name: "split-slow",
    label: "Split > 0.35s",
    fg: "split-slow-foreground",
    note: "Okabe-Ito vermillion · 4.0:1 on white (AA UI)",
  },
  {
    name: "split-transition",
    label: "First / transition / reload",
    fg: "split-transition-foreground",
    note: "Okabe-Ito blue · 5.5:1 on white (AA text)",
  },
] as const;

const STATUS_TOKENS = [
  "status-not-started",
  "status-in-progress",
  "status-complete",
  "status-warning",
] as const;

const MARKER_TOKENS = ["marker-detected", "marker-rejected", "marker-manual"] as const;

export function Design() {
  const { theme, resolved, setTheme } = useTheme();

  return (
    <div className="space-y-12 pb-12">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Design system</h1>
        <p className="text-sm text-muted-foreground">
          Live spec for splitsmith UI v1. Tokens, components, and patterns.
          Mode: <Badge variant="outline">{theme}</Badge>{" "}
          (resolved: <Badge variant="outline">{resolved}</Badge>).
        </p>
        <div className="flex gap-2 pt-2">
          <Button size="sm" variant={theme === "light" ? "default" : "outline"} onClick={() => setTheme("light")}>
            Light
          </Button>
          <Button size="sm" variant={theme === "dark" ? "default" : "outline"} onClick={() => setTheme("dark")}>
            Dark
          </Button>
          <Button size="sm" variant={theme === "system" ? "default" : "outline"} onClick={() => setTheme("system")}>
            System
          </Button>
        </div>
      </header>

      {/* ------------------------------------------------------------------ */}
      <Section
        title="Color tokens — shadcn semantic"
        description="The standard shadcn palette. Exposed as Tailwind utility classes (bg-primary, text-muted-foreground, etc.)."
      >
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {SHADCN_TOKENS.map((t) => (
            <ColorSwatch key={t} name={t} />
          ))}
        </div>
      </Section>

      <Section
        title="Color tokens — splits (Okabe-Ito, color-blind safe)"
        description="Distinguishable under deuteranopia, protanopia, and tritanopia. fcpxml_gen.py emits band names ([GREEN]/[YELLOW]/[RED]/[BLUE]) as text only -- FCP renders marker color from FCP-side settings, so changing in-app palette has no effect on FCP output."
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {SPLIT_TOKENS.map((t) => (
            <div
              key={t.name}
              className="rounded-lg border border-border p-4"
              style={{ backgroundColor: `var(--${t.name})`, color: `var(--${t.fg})` }}
            >
              <div className="font-mono text-xs opacity-80">--{t.name}</div>
              <div className="text-base font-semibold">{t.label}</div>
              <div className="mt-1 text-xs opacity-80">{t.note}</div>
            </div>
          ))}
        </div>
      </Section>

      <Section
        title="Color tokens — status & markers"
        description="Stage status and audit-marker colors. Used in the overview, ingest, and audit screens."
      >
        <div>
          <h3 className="mb-2 text-sm font-medium">Status</h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {STATUS_TOKENS.map((t) => (
              <ColorSwatch key={t} name={t} />
            ))}
          </div>
        </div>
        <div className="mt-6">
          <h3 className="mb-2 text-sm font-medium">Markers</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {MARKER_TOKENS.map((t) => (
              <ColorSwatch key={t} name={t} />
            ))}
          </div>
        </div>
      </Section>

      {/* ------------------------------------------------------------------ */}
      <Section title="Typography" description="Inter for sans, JetBrains Mono for time displays.">
        <div className="space-y-2">
          <p className="text-3xl font-semibold tracking-tight">3xl Heading — Inter</p>
          <p className="text-2xl font-semibold tracking-tight">2xl Heading</p>
          <p className="text-xl font-medium">xl Title</p>
          <p className="text-lg">lg Subtitle</p>
          <p className="text-base">Base body. The quick brown fox jumps over the lazy dog.</p>
          <p className="text-sm text-muted-foreground">sm muted. Used for help text and captions.</p>
          <p className="text-xs text-muted-foreground">xs muted. Used sparingly for timestamps and metadata.</p>
          <p className="font-mono text-base tabular-nums">
            00:14.732 — JetBrains Mono with tabular-nums
          </p>
          <p className="font-mono text-sm tabular-nums">
            shot 7 / 14 · split 0.213s · confidence 0.624
          </p>
        </div>
      </Section>

      {/* ------------------------------------------------------------------ */}
      <Section title="Buttons" description="All variants and sizes.">
        <div className="space-y-4">
          <Row>
            <Button>Default</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="destructive">Destructive</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="link">Link</Button>
          </Row>
          <Row>
            <Button size="sm">Small</Button>
            <Button>Default</Button>
            <Button size="lg">Large</Button>
            <Button size="icon" aria-label="Add">
              <Plus />
            </Button>
            <Button size="icon" variant="destructive" aria-label="Delete">
              <Trash2 />
            </Button>
          </Row>
          <Row>
            <Button>
              <Save />
              With icon
            </Button>
            <Button variant="outline">
              <Crosshair />
              With icon (outline)
            </Button>
            <Button disabled>Disabled</Button>
          </Row>
        </div>
      </Section>

      {/* ------------------------------------------------------------------ */}
      <Section title="Badges" description="Status, splits, and generic.">
        <div className="space-y-4">
          <Row>
            <Badge>Default</Badge>
            <Badge variant="secondary">Secondary</Badge>
            <Badge variant="destructive">Destructive</Badge>
            <Badge variant="outline">Outline</Badge>
          </Row>
          <Row>
            <Badge variant="good">0.21s</Badge>
            <Badge variant="ok">0.31s</Badge>
            <Badge variant="slow">0.42s</Badge>
            <Badge variant="transition">draw / 1.42s</Badge>
          </Row>
          <Row>
            <Badge variant="statusNotStarted" className="gap-1">
              ○ Not started
            </Badge>
            <Badge variant="statusInProgress" className="gap-1">
              ◐ In progress
            </Badge>
            <Badge variant="statusComplete" className="gap-1">
              ● Complete
            </Badge>
            <Badge variant="statusWarning" className="gap-1">
              ▲ Needs attention
            </Badge>
          </Row>
        </div>
      </Section>

      {/* ------------------------------------------------------------------ */}
      <Section title="Cards" description="The default container pattern.">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Stage 3 — Per told me to do it</CardTitle>
              <CardDescription>14.74s · 14 shots audited</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Primary video</span>
                <span className="font-mono text-xs">VID_20260426_162417.mp4</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Beep</span>
                <span className="font-mono tabular-nums">12.453s</span>
              </div>
              <div className="flex gap-2 pt-2">
                <Badge variant="statusComplete">Audited</Badge>
                <Badge variant="outline">2 angles</Badge>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Loading state</CardTitle>
              <CardDescription>Skeleton lines for incoming data.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-4 w-2/3" />
            </CardContent>
          </Card>
        </div>
      </Section>

      {/* ------------------------------------------------------------------ */}
      <Section
        title="Marker glyphs — color + shape"
        description="Audit waveform markers. Each state has a distinct shape so users with color vision deficiencies can tell them apart without color cues. WCAG 1.4.1 (Use of Color)."
      >
        <div className="rounded-lg border border-border bg-muted/30 p-6">
          <div className="grid grid-cols-3 gap-6">
            <MarkerSpec
              kind="detected"
              title="Detected"
              note="Filled triangle. Detector candidate the user accepted. Default state."
            />
            <MarkerSpec
              kind="rejected"
              title="Rejected"
              note="Outline triangle with strikethrough. Detector candidate the user dropped."
            />
            <MarkerSpec
              kind="manual"
              title="Manual"
              note="Dashed diamond. User-added shot the detector missed."
            />
          </div>
          <p className="mt-4 text-xs text-muted-foreground">
            All three shapes are recognisable at 12px and at 32px, in light and
            dark mode, and under simulated deuteranopia / protanopia / tritanopia.
          </p>
        </div>
      </Section>

      {/* ------------------------------------------------------------------ */}
      <Section
        title="Accessibility"
        description="WCAG 2.2 Level AA target. The locked commitments and audit checklist for the production UI."
      >
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Eye className="size-5" />
                Color is never the only signal
              </CardTitle>
              <CardDescription>WCAG 1.4.1 Use of Color</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-center gap-2">
                <MarkerGlyph kind="detected" />
                <span>Shape encodes meaning, color reinforces it.</span>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="good" className="gap-1">
                  <Check className="size-3" /> 0.21s
                </Badge>
                <span>Split badges pair color with the value (text).</span>
              </div>
              <p className="text-muted-foreground">
                Status badges, marker glyphs, and split colors all carry a
                non-color signal (icon, shape, or text).
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Color-blind safety — Okabe-Ito</CardTitle>
              <CardDescription>
                Empirically distinguishable across the three common
                dichromacies.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                <li>
                  <span className="text-foreground">Deuteranopia / -anomaly</span>{" "}
                  (most common, ~6% of men): bluish green vs vermillion stay
                  distinct.
                </li>
                <li>
                  <span className="text-foreground">Protanopia / -anomaly</span>:
                  same — vermillion shifts but still differs from bluish green.
                </li>
                <li>
                  <span className="text-foreground">Tritanopia / -anomaly</span>{" "}
                  (rare): yellow vs blue separation handled by the palette
                  choice.
                </li>
                <li>
                  <span className="text-foreground">Achromatopsia</span>{" "}
                  (greyscale): rely on shapes + text labels.
                </li>
              </ul>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Contrast</CardTitle>
              <CardDescription>WCAG 1.4.3 + 1.4.11</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-muted-foreground">
              <p>
                Body text ≥ 4.5:1 against background. UI components (icons,
                borders, button outlines) ≥ 3:1.
              </p>
              <p>
                Split colors meet at-least UI contrast against their containing
                surface; foreground text on each split swatch is paired for
                ≥ 4.5:1 (white on vermillion / blue / bluish green; near-black
                on yellow).
              </p>
              <p>
                shadcn semantic tokens are AA-clean by default in both light and
                dark mode.
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Keyboard &amp; reduced motion</CardTitle>
              <CardDescription>WCAG 2.1 + 2.3.3</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-muted-foreground">
              <p>
                Every interactive control reachable by Tab. Focus rings visible
                in both light and dark mode (shadcn default).
              </p>
              <p>
                <code>prefers-reduced-motion</code> reduces all transitions to
                near-zero (see Motion section above).
              </p>
              <p>
                Marker drag in the audit screen has keyboard equivalents
                (arrows step, Enter accepts) — see #15.
              </p>
            </CardContent>
          </Card>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          Full audit checklist tracked separately. The Accessibility tracking
          issue lists the per-screen items.
        </p>
      </Section>

      {/* ------------------------------------------------------------------ */}
      <Section title="Motion" description="Hover transitions and reduced-motion behavior.">
        <p className="text-sm text-muted-foreground">
          Hover the buttons above. Transitions are 150ms ease-out. Respects{" "}
          <code>prefers-reduced-motion</code>.
        </p>
      </Section>
    </div>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      <div>{children}</div>
    </section>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2">{children}</div>;
}

function ColorSwatch({ name }: { name: string }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div
        className="h-16 w-full"
        style={{ backgroundColor: `var(--${name})` }}
      />
      <div className="px-3 py-2">
        <div className="font-mono text-xs">--{name}</div>
      </div>
    </div>
  );
}

function MarkerSpec({
  kind,
  title,
  note,
}: {
  kind: "detected" | "rejected" | "manual";
  title: string;
  note: string;
}) {
  return (
    <div className="flex flex-col items-center gap-2 text-center">
      <div className="flex items-end gap-3">
        <MarkerGlyph kind={kind} size={32} />
        <MarkerGlyph kind={kind} size={20} />
        <MarkerGlyph kind={kind} size={12} />
      </div>
      <div>
        <div className="text-sm font-medium">{title}</div>
        <div className="text-xs text-muted-foreground">{note}</div>
      </div>
    </div>
  );
}
