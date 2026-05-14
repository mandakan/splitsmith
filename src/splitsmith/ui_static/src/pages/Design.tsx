/**
 * /_design -- visual spec for the Shot Timer redesign.
 *
 * Renders every canonical token (color / typography / radius / shadow) and
 * every foundational primitive in all variants. A mode toggle at the top
 * lets reviewers verify the Match/Developer accent flip in-place.
 *
 * Issue #319. Will grow as later surfaces add primitives.
 */

import { Bell, Check, Crosshair, Plus, Save, Settings, Trash2 } from "lucide-react";

import { Avatar, AvatarStack } from "@/components/ui/AvatarStack";
import { Brand, BrandMark } from "@/components/ui/Brand";
import { Breadcrumb, ContextBar } from "@/components/ui/ContextBar";
import { DisplayHeading } from "@/components/ui/DisplayHeading";
import { IconButton } from "@/components/ui/IconButton";
import { Kbd } from "@/components/ui/Kbd";
import { Kicker } from "@/components/ui/Kicker";
import { ModeSwitch } from "@/components/ui/ModeSwitch";
import { Readout } from "@/components/ui/Readout";
import { StatusPill } from "@/components/ui/StatusPill";
import { Tick, TickStrip, type TickState } from "@/components/ui/Tick";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useMode } from "@/lib/mode";

/* ---------------------------------------------------------------------------
 * Token tables -- kept in step with styles/index.css.
 * ------------------------------------------------------------------------- */

const SURFACE_TOKENS = ["bg", "bg-glow", "surface", "surface-2", "surface-3", "surface-4"];
const TEXT_TOKENS = ["ink", "ink-2", "muted", "subtle", "whisper"];
const RULE_TOKENS = ["rule", "rule-strong"];
const BRAND_TOKENS = ["led", "led-soft", "led-deep"];
const STATUS_TOKENS = ["live", "done", "cold", "beep", "manual"];
const SHOOTER_TOKENS = [
  { key: "ma", name: "Mathias (you)" },
  { key: "jl", name: "Johan" },
  { key: "pe", name: "Pia" },
  { key: "rj", name: "Robert" },
];

const TYPE_SCALE = [
  { size: "text-xs", px: "10px", label: "xs -- small-caps labels" },
  { size: "text-sm", px: "12px", label: "sm -- meta, captions" },
  { size: "text-md", px: "14px", label: "md -- body sm" },
  { size: "text-base", px: "15px", label: "base -- body" },
  { size: "text-lg", px: "16px", label: "lg -- emphasised body" },
  { size: "text-xl", px: "20px", label: "xl -- subhead" },
  { size: "text-2xl", px: "24px", label: "2xl -- card titles" },
  { size: "text-3xl", px: "28px", label: "3xl -- match titles" },
  { size: "text-4xl", px: "40px", label: "4xl -- big readouts" },
];

export function Design() {
  const { mode } = useMode();

  return (
    <div className="space-y-12 pb-16 font-sans text-ink">
      {/* ----- Page header ---------------------------------------------- */}
      <header className="space-y-4">
        <Kicker>Project Register · Vol. 01 · Ed. 04</Kicker>
        <DisplayHeading variant="hero" as="h1">
          Design system
        </DisplayHeading>
        <p className="max-w-2xl text-sm text-muted">
          Shot Timer instrument-panel system. Every token, every foundational
          primitive on a single scrollable page. Mode flip is live --
          everything tagged "accent" follows it.
        </p>
        <div className="flex items-center gap-3 pt-2">
          <ModeSwitch />
          <span className="font-mono text-xs uppercase tracking-[0.16em] text-muted">
            current: {mode}
          </span>
        </div>
      </header>

      {/* ----- Surfaces ------------------------------------------------- */}
      <Section title="Surfaces" kicker="01 / Color">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          {SURFACE_TOKENS.map((t) => (
            <Swatch key={t} token={t} />
          ))}
        </div>
      </Section>

      {/* ----- Text ----------------------------------------------------- */}
      <Section title="Text" kicker="02 / Color">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {TEXT_TOKENS.map((t) => (
            <TextSwatch key={t} token={t} />
          ))}
        </div>
      </Section>

      {/* ----- Rules ---------------------------------------------------- */}
      <Section title="Rules" kicker="03 / Color">
        <div className="grid grid-cols-2 gap-3">
          {RULE_TOKENS.map((t) => (
            <RuleSwatch key={t} token={t} />
          ))}
        </div>
      </Section>

      {/* ----- Brand + status ------------------------------------------- */}
      <Section title="Brand / status" kicker="04 / Color">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-8">
          {BRAND_TOKENS.map((t) => (
            <Swatch key={t} token={t} />
          ))}
          {STATUS_TOKENS.map((t) => (
            <Swatch key={t} token={t} />
          ))}
        </div>
      </Section>

      {/* ----- Per-shooter ---------------------------------------------- */}
      <Section title="Per-shooter identity" kicker="05 / Color">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {SHOOTER_TOKENS.map((s) => (
            <ShooterSwatch key={s.key} keyName={s.key} name={s.name} />
          ))}
        </div>
      </Section>

      {/* ----- Typography ----------------------------------------------- */}
      <Section title="Typography" kicker="06 / Type">
        <div className="grid gap-6 md:grid-cols-2">
          <div className="space-y-4 rounded-lg border border-rule bg-surface p-6">
            <Kicker tone="muted">Antonio · display</Kicker>
            <div className="font-display text-5xl font-bold uppercase tracking-tight">
              VADS Easter
            </div>
            <div className="font-display text-2xl font-bold uppercase tracking-tight text-ink-2">
              Card title
            </div>
            <div className="font-display text-base font-semibold uppercase tracking-[0.14em] text-muted">
              Section label
            </div>
          </div>
          <div className="space-y-4 rounded-lg border border-rule bg-surface p-6">
            <Kicker tone="muted">Geist · body</Kicker>
            <p className="text-base">
              Geist sets the body voice. Comfortable at 15px on dark
              surfaces, with subtle stylistic alternates enabled.
            </p>
            <p className="text-sm text-muted">
              Smaller meta line in muted ink. Sentence length stays
              reasonable so a single line never wraps the column.
            </p>
          </div>
          <div className="space-y-4 rounded-lg border border-rule bg-surface p-6 md:col-span-2">
            <Kicker tone="muted">JetBrains Mono · numerals</Kicker>
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-3 font-mono tabular-nums">
              <span className="text-5xl text-led">08.218</span>
              <span className="text-3xl text-ink-2">9.114</span>
              <span className="text-2xl text-muted">+04.182s</span>
              <span className="text-base text-muted">2026-04-12 11:21:31</span>
            </div>
          </div>
        </div>
        <div className="rounded-lg border border-rule bg-surface">
          <table className="w-full text-sm">
            <thead className="border-b border-rule text-left">
              <tr className="text-muted">
                <th className="px-4 py-3 font-mono text-xs uppercase tracking-wider">Token</th>
                <th className="px-4 py-3 font-mono text-xs uppercase tracking-wider">Size</th>
                <th className="px-4 py-3 font-mono text-xs uppercase tracking-wider">Example</th>
              </tr>
            </thead>
            <tbody>
              {TYPE_SCALE.map((row) => (
                <tr key={row.size} className="border-b border-rule/60 last:border-b-0">
                  <td className="px-4 py-3 font-mono text-xs text-muted">{row.size}</td>
                  <td className="px-4 py-3 font-mono text-xs text-muted">{row.px}</td>
                  <td className={`px-4 py-3 ${row.size}`}>{row.label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {/* ----- Radii / shadows ----------------------------------------- */}
      <Section title="Radii / shadows" kicker="07 / Surface">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {["sm", "md", "lg", "xl", "2xl", "3xl", "full"].map((r) => (
            <div key={r} className="flex flex-col items-center gap-2">
              <div
                className="size-16 bg-surface-3"
                style={{ borderRadius: `var(--radius-${r})` }}
              />
              <span className="font-mono text-xs text-muted">radius-{r}</span>
            </div>
          ))}
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {[
            { name: "shadow-card", style: "0 1px 0 rgba(255,255,255,0.02) inset, 0 18px 40px -24px rgba(0,0,0,0.7)" },
            { name: "shadow-elev", style: "0 1px 0 rgba(255,255,255,0.03) inset, 0 24px 60px -32px rgba(0,0,0,0.8)" },
            { name: "shadow-led", style: "0 0 0 1px var(--color-led), 0 0 24px var(--color-led-glow)" },
          ].map((s) => (
            <div
              key={s.name}
              className="rounded-xl bg-surface-2 p-6 text-center"
              style={{ boxShadow: s.style }}
            >
              <span className="font-mono text-xs uppercase tracking-wider text-muted">
                {s.name}
              </span>
            </div>
          ))}
        </div>
      </Section>

      {/* ----- Primitives: Brand --------------------------------------- */}
      <Section title="Brand" kicker="08 / Primitive">
        <div className="flex flex-wrap items-center gap-12 rounded-lg border border-rule bg-surface p-8">
          <Brand serial="Vol. 01 · Ed. 04" />
          <Brand variant="compact" />
          <BrandMark className="size-12" />
        </div>
      </Section>

      {/* ----- Primitives: Buttons + IconButton + Kbd ------------------ */}
      <Section title="Buttons" kicker="09 / Primitive">
        <Block>
          <Button>
            <Plus className="size-4" />
            Primary
          </Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="outline">
            <Save className="size-4" /> Outline
          </Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="destructive">
            <Trash2 className="size-4" /> Destructive
          </Button>
          <Button size="sm">Small</Button>
          <Button size="lg">Large</Button>
          <Button disabled>Disabled</Button>
        </Block>
        <Block>
          <IconButton label="Notifications">
            <Bell />
          </IconButton>
          <IconButton label="Settings" variant="subtle">
            <Settings />
          </IconButton>
          <IconButton label="Crosshair" variant="led">
            <Crosshair />
          </IconButton>
          <IconButton label="Notifications" size="sm">
            <Bell />
          </IconButton>
          <IconButton label="Notifications" size="lg">
            <Bell />
          </IconButton>
        </Block>
        <Block>
          <span className="flex items-center gap-1.5 text-sm text-muted">
            Save with <Kbd>⌘</Kbd> <Kbd>S</Kbd>
          </span>
          <span className="flex items-center gap-1.5 text-sm text-muted">
            Quick switch <Kbd size="md">⌘</Kbd> <Kbd size="md">K</Kbd>
          </span>
        </Block>
      </Section>

      {/* ----- Primitives: Status / Pills / Ticks ---------------------- */}
      <Section title="Status pills & ticks" kicker="10 / Primitive">
        <Block>
          <StatusPill tone="in-progress">
            <Check className="size-3" /> In progress
          </StatusPill>
          <StatusPill tone="exported">Exported</StatusPill>
          <StatusPill tone="archived">Archived</StatusPill>
          <StatusPill tone="beep">Beep review</StatusPill>
          <StatusPill tone="led">LED accent</StatusPill>
        </Block>
        <div className="space-y-3 rounded-lg border border-rule bg-surface p-4">
          <Kicker tone="muted">Tick states</Kicker>
          <div className="flex items-end gap-4">
            <span className="flex flex-col items-center gap-1">
              <Tick state="todo" />
              <span className="font-mono text-[0.625rem] text-muted">todo</span>
            </span>
            <span className="flex flex-col items-center gap-1">
              <Tick state="done" />
              <span className="font-mono text-[0.625rem] text-muted">done</span>
            </span>
            <span className="flex flex-col items-center gap-1">
              <Tick state="flagged" />
              <span className="font-mono text-[0.625rem] text-muted">flag</span>
            </span>
            <span className="flex flex-col items-center gap-1">
              <Tick state="current" />
              <span className="font-mono text-[0.625rem] text-muted">now</span>
            </span>
          </div>
          <div className="mt-4 space-y-2">
            <Kicker tone="muted">TickStrip · 12 stages, stage 8 current, 1 flagged</Kicker>
            <TickStrip
              states={STAGE_DEMO}
              ariaLabel="6 of 12 stages complete, 1 flagged, currently on stage 8"
            />
          </div>
        </div>
      </Section>

      {/* ----- Primitives: Avatar / AvatarStack ------------------------ */}
      <Section title="Shooters" kicker="11 / Primitive">
        <Block>
          <Avatar initials="MA" tone="you" name="Mathias Axell" size="lg" />
          <Avatar initials="JL" tone="jl" name="Johan Larsson" size="lg" />
          <Avatar initials="PE" tone="pe" name="Pia Eriksson" size="lg" />
          <Avatar initials="RJ" tone="rj" name="Robert Janson" size="lg" />
        </Block>
        <Block>
          <AvatarStack
            size="md"
            avatars={[
              { initials: "MA", tone: "you", name: "Mathias" },
              { initials: "JL", tone: "jl", name: "Johan" },
              { initials: "PE", tone: "pe", name: "Pia" },
              { initials: "RJ", tone: "rj", name: "Robert" },
            ]}
          />
          <AvatarStack
            size="sm"
            overflow={3}
            avatars={[
              { initials: "MA", tone: "you" },
              { initials: "JL", tone: "jl" },
              { initials: "PE", tone: "pe" },
            ]}
          />
        </Block>
      </Section>

      {/* ----- Primitives: Readout ------------------------------------- */}
      <Section title="Readouts" kicker="12 / Primitive">
        <div className="grid grid-cols-2 gap-4 rounded-lg border border-rule bg-surface p-6 md:grid-cols-4">
          <Readout label="Stage time" value="08.218" unit="s" tone="led" size="lg" />
          <Readout label="Avg split" value="0.548" unit="s" trailing="vs. 0.612 prior" />
          <Readout label="Status" value="LIVE" tone="live" trailing="recording" />
          <Readout label="Exported" value="14 / 16" tone="done" />
        </div>
      </Section>

      {/* ----- Primitives: ContextBar ---------------------------------- */}
      <Section title="Context bar" kicker="13 / Primitive">
        <div className="overflow-hidden rounded-lg border border-rule bg-bg">
          <ContextBar
            trailing={
              <span className="font-mono text-xs uppercase tracking-[0.14em] text-muted">
                12 / 16 audited
              </span>
            }
          >
            <Breadcrumb
              items={[
                { label: "Matches", href: "#" },
                { label: "VADS Easter Shoot 2026", href: "#" },
                { label: "Stage 02 · Tower" },
              ]}
            />
          </ContextBar>
        </div>
      </Section>

      {/* ----- Composition smoke test ---------------------------------- */}
      <Section title="Composition" kicker="14 / Smoke test">
        <Card className="border-rule bg-surface text-ink">
          <CardHeader>
            <CardTitle className="flex items-center gap-3">
              <Avatar initials="MA" tone="you" name="Mathias" size="sm" />
              <DisplayHeading variant="card" as="span">
                Stage 02 · Tower
              </DisplayHeading>
              <StatusPill tone="in-progress" className="ml-auto">
                Audit in progress
              </StatusPill>
            </CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <Readout label="Stage time" value="08.218" unit="s" tone="led" size="lg" />
            <Readout label="Avg split" value="0.548" unit="s" />
            <Readout label="Fastest" value="0.337" unit="s" tone="done" />
            <Readout label="Shots" value="16 / 16" />
          </CardContent>
        </Card>
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Helper components
 * ------------------------------------------------------------------------- */

function Section({
  title,
  kicker,
  children,
}: {
  title: string;
  kicker: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-4">
      <div className="flex items-baseline gap-3 border-b border-rule pb-3">
        <Kicker>{kicker}</Kicker>
        <DisplayHeading variant="section" as="h2">
          {title}
        </DisplayHeading>
      </div>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

function Block({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-rule bg-surface p-4">
      {children}
    </div>
  );
}

function Swatch({ token }: { token: string }) {
  return (
    <div className="flex flex-col gap-2">
      <div
        className="h-16 rounded-md border border-rule"
        style={{ background: `var(--color-${token})` }}
      />
      <div className="flex items-center justify-between font-mono text-[0.6875rem]">
        <span className="text-ink-2">{token}</span>
      </div>
    </div>
  );
}

function TextSwatch({ token }: { token: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-rule bg-surface p-3">
      <span className="text-base" style={{ color: `var(--color-${token})` }}>
        Sample
      </span>
      <span className="font-mono text-[0.6875rem] text-muted">{token}</span>
    </div>
  );
}

function RuleSwatch({ token }: { token: string }) {
  return (
    <div className="flex items-center gap-3 rounded-md bg-surface p-3">
      <div className="h-px flex-1" style={{ background: `var(--color-${token})` }} />
      <span className="font-mono text-[0.6875rem] text-muted">{token}</span>
    </div>
  );
}

function ShooterSwatch({ keyName, name }: { keyName: string; name: string }) {
  return (
    <div className="space-y-3 rounded-lg border border-rule bg-surface p-4">
      <div className="flex items-center gap-3">
        <Avatar
          initials={keyName.toUpperCase()}
          tone={keyName === "ma" ? "you" : (keyName as "jl" | "pe" | "rj")}
          name={name}
          size="md"
        />
        <div className="flex flex-col leading-tight">
          <span className="font-display text-sm font-semibold uppercase tracking-wider">
            {name}
          </span>
          <span className="font-mono text-[0.6875rem] text-muted">
            --color-shooter-{keyName}
          </span>
        </div>
      </div>
      <div className="grid grid-cols-5 gap-1.5">
        {["", "-soft", "-deep", "-glow", "-tint"].map((suffix) => (
          <div
            key={suffix}
            className="h-6 rounded-sm border border-rule"
            style={{ background: `var(--color-shooter-${keyName}${suffix})` }}
            title={`--color-shooter-${keyName}${suffix}`}
          />
        ))}
      </div>
    </div>
  );
}

const STAGE_DEMO: TickState[] = [
  "done",
  "done",
  "done",
  "done",
  "flagged",
  "done",
  "done",
  "current",
  "todo",
  "todo",
  "todo",
  "todo",
];
