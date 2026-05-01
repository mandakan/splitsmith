/**
 * /_design — the visual spec for the production UI.
 *
 * Per issue #12: "if a component renders correctly here, it renders correctly
 * in the app." Render every token, every shadcn component, every custom
 * component on a single scrollable page so visual QA is one page reload away.
 */

import { Crosshair, Plus, Save, Trash2 } from "lucide-react";

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
  { name: "split-good", label: "Split ≤ 0.25s", fg: "split-good-foreground" },
  { name: "split-ok", label: "Split 0.25–0.35s", fg: "split-ok-foreground" },
  { name: "split-slow", label: "Split > 0.35s", fg: "split-slow-foreground" },
  { name: "split-transition", label: "First / transition / reload", fg: "split-transition-foreground" },
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
        title="Color tokens — splits"
        description="Match fcpxml_gen.py hex values. Same shot rendered green here is the same shot rendered green in Final Cut Pro."
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {SPLIT_TOKENS.map((t) => (
            <div
              key={t.name}
              className={`rounded-lg border border-border p-4 bg-${t.name} text-${t.fg}`}
              style={{ backgroundColor: `var(--${t.name})`, color: `var(--${t.fg})` }}
            >
              <div className="font-mono text-xs opacity-90">--{t.name}</div>
              <div className="text-base font-semibold">{t.label}</div>
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
            <Badge variant="statusNotStarted">Not started</Badge>
            <Badge variant="statusInProgress">In progress</Badge>
            <Badge variant="statusComplete">Complete</Badge>
            <Badge variant="statusWarning">Needs attention</Badge>
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
      <Section title="Marker glyphs" description="Audit waveform markers, side-by-side for contrast checks.">
        <div className="rounded-lg border border-border bg-muted/30 p-6">
          <div className="flex items-end gap-8">
            <Marker label="Detected" color="marker-detected" />
            <Marker label="Manual" color="marker-manual" dashed />
            <Marker label="Rejected" color="marker-rejected" rejected />
          </div>
        </div>
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

function Marker({
  label,
  color,
  dashed,
  rejected,
}: {
  label: string;
  color: string;
  dashed?: boolean;
  rejected?: boolean;
}) {
  return (
    <div className="flex flex-col items-center gap-2">
      <div
        className="h-16 w-1.5 rounded-sm"
        style={{
          backgroundColor: `var(--${color})`,
          opacity: rejected ? 0.5 : 1,
          borderLeft: dashed ? "2px dashed currentColor" : undefined,
          textDecoration: rejected ? "line-through" : undefined,
        }}
      />
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}
