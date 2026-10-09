/**
 * You (spec 2026-10-08): who you are as a shooter, your look on the cards
 * and your brand on every video you render. Every other shooter's look is on
 * the Shooters page (spec 2026-10-09). Both modes: the Account page
 * links here hosted, the Matches header locally. Rules live in `lib/you`.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Field, inputClass } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import {
  api,
  apiErrorText,
  type AccountProfileView,
  type ScoreboardIdentity,
  type ScoreboardShooterRef,
  type ShooterBookEntryView,
} from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { dismissNewChip } from "@/lib/useWhatsNew";
import { cn } from "@/lib/utils";
import { YOU_FEATURE, pinBody } from "@/lib/you";

const ACCENT_SWATCHES = ["#ff2d2d", "#fbbf24", "#4ade80", "#60a5fa", "#c084fc", "#f472b6"] as const;
const HEX = /^#[0-9a-fA-F]{6}$/;
const BRAND_LINE_MAX = 60;
const CLUB_MAX = 60;

function Section({ label, id, children }: { label: string; id?: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24 rounded-[10px] border border-rule bg-surface">
      <div className="border-b border-rule px-3.5 py-2">
        <Label>{label}</Label>
      </div>
      {children}
    </section>
  );
}

function FilePick({ onPick, label, disabled }: { onPick: (f: File) => void; label: string; disabled?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        aria-label={label}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onPick(file);
          e.target.value = "";
        }}
      />
      <Button size="sm" variant="default" disabled={disabled} onClick={() => input.current?.click()}>
        Choose
      </Button>
    </>
  );
}

function YouShooter({ me, onChange }: { me: ScoreboardIdentity | null; onChange: (me: ScoreboardIdentity | null) => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<ScoreboardShooterRef[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const search = async () => {
    setBusy(true);
    setError(null);
    try {
      setResults(await api.searchShooterIndex(q));
    } catch (e) {
      setError(apiErrorText(e, "The shooter index could not be reached."));
    } finally {
      setBusy(false);
    }
  };

  const pick = async (ref: ScoreboardShooterRef) => {
    setBusy(true);
    try {
      onChange(
        await api.putScoreboardIdentity(pinBody(ref, me)),
      );
      setResults(null);
      setQ("");
    } catch (e) {
      setError(apiErrorText(e, "Could not save who you are."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section label="Shooter">
      <Field
        label="You"
        htmlFor="you-search"
        help="Find yourself in the shooter index. In every match, the shooter with this id is you, and your look below applies to them."
        error={error}
      >
        {me ? (
          <div className="mb-2 flex items-center gap-2 text-md">
            <span className="text-ink">{me.display_name ?? "You"}</span>
            <span className="numeral text-sm text-muted">#{me.shooter_id}</span>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => void api.clearScoreboardIdentity().then(() => onChange(null))}
            >
              Clear
            </Button>
          </div>
        ) : null}
        <div className="flex items-center gap-2">
          <input
            id="you-search"
            className={inputClass}
            placeholder="Your name"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && q.trim()) void search();
            }}
          />
          <Button size="sm" variant="default" disabled={busy || !q.trim()} onClick={() => void search()}>
            Search
          </Button>
        </div>
        {results ? (
          results.length === 0 ? (
            <p className="mt-2 text-sm text-muted">No shooter by that name.</p>
          ) : (
            <ul className="mt-2 divide-y divide-rule border-y border-rule">
              {results.slice(0, 10).map((r) => (
                <li key={r.shooterId} className="flex items-center gap-2 py-1.5 text-md">
                  <span className="min-w-0 flex-1 truncate text-ink">{r.name}</span>
                  <span className="truncate text-sm text-muted">{[r.club, r.division].filter(Boolean).join(", ")}</span>
                  <Button size="sm" variant="default" disabled={busy} onClick={() => void pick(r)}>
                    This is me
                  </Button>
                </li>
              ))}
            </ul>
          )
        ) : null}
      </Field>
    </Section>
  );
}

function YourLook({ me, entry, onSaved }: { me: ScoreboardIdentity; entry: ShooterBookEntryView | null; onSaved: () => void }) {
  const [accent, setAccent] = useState(entry?.identity.accent ?? "");
  const [club, setClub] = useState(entry?.identity.club ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setAccent(entry?.identity.accent ?? "");
    setClub(entry?.identity.club ?? "");
  }, [entry]);
  const valid = accent === "" || HEX.test(accent);
  const logo = entry?.identity.logo ?? null;

  const run = async (op: () => Promise<unknown>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      await op();
      onSaved();
    } catch (e) {
      setError(apiErrorText(e, fallback));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section label="Your look">
      <Field
        label="Accent"
        htmlFor="you-accent"
        help="Your colour on your tile, the stage summary and the roster, in every match you are in."
        error={error}
      >
        <div className="flex flex-wrap items-center gap-2">
          {ACCENT_SWATCHES.map((hex) => (
            <button
              key={hex}
              type="button"
              aria-label={`Accent ${hex}`}
              aria-pressed={accent.toLowerCase() === hex}
              onClick={() => setAccent(hex)}
              className={cn(
                "size-6 rounded-full border border-rule-strong",
                accent.toLowerCase() === hex && "ring-2 ring-led ring-offset-2 ring-offset-surface",
              )}
              style={{ backgroundColor: hex }}
            />
          ))}
          <input
            id="you-accent"
            className={cn(inputClass, "w-28 font-mono")}
            placeholder="#rrggbb"
            value={accent}
            onChange={(e) => setAccent(e.target.value)}
            aria-invalid={!valid}
          />
        </div>
      </Field>
      <Field label="Club" htmlFor="you-club" help="One line under your name on the title page.">
        <input id="you-club" className={inputClass} value={club} maxLength={CLUB_MAX} onChange={(e) => setClub(e.target.value)} />
        <div className="mt-2.5">
          <Button
            size="sm"
            variant="default"
            disabled={busy || !valid}
            onClick={() =>
              void run(
                () =>
                  api.putShooterBookEntry(me.shooter_id, {
                    accent: accent === "" ? null : accent.toLowerCase(),
                    club: club.trim() === "" ? null : club.trim(),
                    label: me.display_name,
                  }),
                "Could not save your look.",
              )
            }
          >
            Save
          </Button>
        </div>
      </Field>
      <Field label="Logo" help="PNG, JPEG or WebP, at most 2 MB. Drawn top-right on the cards.">
        <div className="flex items-center gap-3">
          {logo ? (
            <img
              src={`/api/me/shooter-book/${me.shooter_id}/logo?v=${encodeURIComponent(logo)}`}
              alt="Your logo"
              className="size-12 rounded border border-rule object-contain"
            />
          ) : (
            <span className="text-sm text-muted">No logo</span>
          )}
          <FilePick
            label="Your logo file"
            disabled={busy}
            onPick={(f) => void run(() => api.uploadShooterBookLogo(me.shooter_id, f), "Could not save the logo.")}
          />
          {logo ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => void run(() => api.removeShooterBookLogo(me.shooter_id), "Could not remove the logo.")}
            >
              Remove
            </Button>
          ) : null}
        </div>
      </Field>
    </Section>
  );
}

function YourBrand({ profile, onSaved }: { profile: AccountProfileView | null; onSaved: (p: AccountProfileView) => void }) {
  const [line, setLine] = useState(profile?.brand.line ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setLine(profile?.brand.line ?? ""), [profile]);
  const logo = profile?.brand.logo ?? null;

  const run = async (op: () => Promise<AccountProfileView>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      onSaved(await op());
    } catch (e) {
      setError(apiErrorText(e, fallback));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section label="Your brand" id="brand">
      <Field
        label="Logo"
        help="Your mark, top-left on the title page and the closing card of every video you render. A Look with its own brand shows that instead; turn it off for one video under Details on the Export page."
        error={error}
      >
        <div className="flex items-center gap-3">
          {logo ? (
            <img
              src={`/api/me/profile/brand-logo?v=${encodeURIComponent(logo)}`}
              alt="Your brand logo"
              className="size-12 rounded border border-rule object-contain"
            />
          ) : (
            <span className="text-sm text-muted">No logo</span>
          )}
          <FilePick label="Brand logo file" disabled={busy} onPick={(f) => void run(() => api.uploadAccountBrandLogo(f), "Could not save the logo.")} />
          {logo ? (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => void run(() => api.removeAccountBrandLogo(), "Could not remove the logo.")}>
              Remove
            </Button>
          ) : null}
        </div>
      </Field>
      <Field label="Line" htmlFor="you-brand-line" help="Beside the logo: a club, a channel, a sponsor.">
        <input
          id="you-brand-line"
          className={inputClass}
          value={line}
          maxLength={BRAND_LINE_MAX}
          onChange={(e) => setLine(e.target.value)}
        />
        <div className="mt-2.5">
          <Button
            size="sm"
            variant="default"
            disabled={busy}
            onClick={() => void run(() => api.putAccountProfile({ brand_line: line }), "Could not save the line.")}
          >
            Save
          </Button>
        </div>
      </Field>
    </Section>
  );
}

export function You() {
  const { mode } = useDeploymentMode();
  // The account menu's Branding links to ``/you#brand``; a client-side
  // navigation never does the browser's anchor scroll, so do it here.
  const { hash } = useLocation();
  useEffect(() => {
    if (!hash) return;
    document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView({ block: "start" });
  }, [hash]);
  const [me, setMe] = useState<ScoreboardIdentity | null>(null);
  const [profile, setProfile] = useState<AccountProfileView | null>(null);
  const [book, setBook] = useState<ShooterBookEntryView[]>([]);
  const [error, setError] = useState<string | null>(null);

  const reloadBook = useCallback(() => {
    api
      .getShooterBook()
      .then((r) => setBook(r.entries))
      .catch((e: unknown) => setError(apiErrorText(e, "Could not read the shooter book.")));
  }, []);

  useEffect(() => {
    dismissNewChip(YOU_FEATURE);
    api.getScoreboardIdentity().then(setMe, () => setMe(null));
    api.getAccountProfile().then(setProfile, (e: unknown) => setError(apiErrorText(e, "Could not read your brand.")));
    reloadBook();
  }, [reloadBook]);

  const myEntry = me ? (book.find((e) => e.shooter_id === me.shooter_id) ?? null) : null;
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-7 py-5">
      <PageHeader
        title="You"
        sub="Set once, on every video"
        back={mode === "hosted" ? { label: "Account", to: "/account" } : { label: "Matches", to: "/pick" }}
      />
      {error ? <p className="text-sm text-led-text">{error}</p> : null}
      <YouShooter me={me} onChange={setMe} />
      {me ? <YourLook me={me} entry={myEntry} onSaved={reloadBook} /> : null}
      <YourBrand profile={profile} onSaved={setProfile} />
      <Section label="Shooters">
        <p className="px-3.5 py-3 text-md text-ink-2">
          The looks of everyone you have filmed live on{" "}
          <Link to="/shooters" className="text-ink underline-offset-4 hover:underline">
            Shooters
          </Link>
          .
        </p>
      </Section>
    </div>
  );
}

export default You;
