/**
 * ShooterSheet -- a shooter's look (spec 2026-10-09): the accent that tells
 * their tile and summary apart, the logo the cards draw top-right, and the
 * club line under their name on the title page. Edits the shooter book only,
 * keyed by the SSI shooter id, so the look follows them into every match
 * (the book wins over a match's own record). Opened from the Shooters page
 * and from a shooter pill in a match.
 */
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { LogoGuide } from "@/components/shooters/LogoGuide";
import { Avatar } from "@/components/ui/AvatarStack";
import { Field, inputClass } from "@/components/ui/Field";
import { Sheet } from "@/components/ui/Sheet";
import { ApiError, api } from "@/lib/api";
import { initials } from "@/lib/shooters";
import { cn } from "@/lib/utils";

/** The shipped Look's accent series, offered as the quick picks. */
const ACCENT_SWATCHES = ["#ff2d2d", "#fbbf24", "#4ade80", "#60a5fa", "#c084fc", "#f472b6"] as const;
const HEX = /^#[0-9a-fA-F]{6}$/;
const CLUB_MAX = 60;
/** A logo URL already served from the book; anything else (a match's own
 *  record, which the videos draw while the book has no entry) is copied
 *  into the book on the first save, or saving would drop it. */
const BOOK_LOGO = "/api/me/shooter-book/";

async function copyIntoBook(shooterId: number, url: string): Promise<void> {
  const response = await fetch(url);
  if (!response.ok) return;
  const blob = await response.blob();
  await api.uploadShooterBookLogo(shooterId, new File([blob], "logo", { type: blob.type }));
}

/** The shooter the sheet edits, as the caller knows them. */
export interface SheetShooter {
  shooterId: number;
  name: string;
  accent: string | null;
  club: string | null;
  /** The logo the videos draw now, as a URL, or null. */
  logoUrl: string | null;
}

export interface ShooterSheetProps {
  open: boolean;
  onClose: () => void;
  shooter: SheetShooter | null;
  /** After anything was saved. */
  onChanged: () => void;
}

export function ShooterSheet({ open, onClose, shooter, onChanged }: ShooterSheetProps) {
  const [accent, setAccent] = useState("");
  const [club, setClub] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [removeLogo, setRemoveLogo] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Reset when the sheet opens or turns to another shooter, never because
  // the caller re-rendered: the Footage page rebuilds ``shooter`` on every
  // jobs poll, and keying on the object wiped what was being typed.
  const shooterId = shooter?.shooterId ?? null;
  useEffect(() => {
    if (!open || !shooter) return;
    setAccent(shooter.accent ?? "");
    setClub(shooter.club ?? "");
    setFile(null);
    setRemoveLogo(false);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset per opening and per shooter only
  }, [open, shooterId]);

  useEffect(() => {
    if (!file) {
      setPicked(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPicked(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  if (!shooter) return null;
  const accentValid = accent === "" || HEX.test(accent);
  const shownLogo = picked ?? (removeLogo ? null : shooter.logoUrl);

  const save = async () => {
    if (!accentValid) {
      setError("The accent is a six-digit hex colour, like #ff2d2d.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.putShooterBookEntry(shooter.shooterId, {
        accent: accent === "" ? null : accent.toLowerCase(),
        club: club.trim() === "" ? null : club.trim(),
        label: shooter.name,
      });
      if (file) await api.uploadShooterBookLogo(shooter.shooterId, file);
      else if (removeLogo && shooter.logoUrl) await api.removeShooterBookLogo(shooter.shooterId);
      else if (shooter.logoUrl && !shooter.logoUrl.startsWith(BOOK_LOGO)) {
        await copyIntoBook(shooter.shooterId, shooter.logoUrl);
      }
      onChanged();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save the look.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet open={open} onClose={onClose} label={`Look for ${shooter.name}`}>
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-3 border-b border-rule px-3.5 py-3">
          <Avatar
            initials={initials(shooter.name)}
            seed={shooter.name}
            size="lg"
            accent={accentValid && accent ? accent : null}
            logo={shownLogo}
          />
          <div className="min-w-0">
            <div className="truncate text-md font-medium text-ink">{shooter.name}</div>
            <p className="text-sm text-muted">Drawn in every video of theirs, in every match.</p>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          <div className="border-b border-rule px-3.5 py-3">
            <LogoGuide shooter={shownLogo} highlight="shooter" />
          </div>
          <Field
            label="Shooter logo"
            help="Often their club badge. Top right on their title page, stage slates and closing card. PNG, JPEG or WebP, at most 2 MB."
          >
            <div className="flex items-center gap-3">
              {shownLogo ? (
                <img src={shownLogo} alt="" className="size-12 rounded border border-rule object-contain" />
              ) : (
                <span className="text-sm text-muted">No logo</span>
              )}
              <input
                ref={fileInput}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                aria-label="Logo file"
                className="hidden"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setRemoveLogo(false);
                }}
              />
              <Button size="sm" variant="default" onClick={() => fileInput.current?.click()}>
                {shownLogo ? "Replace" : "Choose"}
              </Button>
              {shownLogo ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setFile(null);
                    setRemoveLogo(true);
                  }}
                >
                  Remove
                </Button>
              ) : null}
            </div>
          </Field>
          <Field
            label="Accent"
            htmlFor="shooter-accent"
            help="Tints their summary bar, their name and the ring on their avatar; blank leaves the Look's colours."
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
                id="shooter-accent"
                className={cn(inputClass, "w-28 font-mono")}
                placeholder="#rrggbb"
                value={accent}
                onChange={(e) => setAccent(e.target.value)}
                aria-invalid={!accentValid}
              />
            </div>
          </Field>
          <Field label="Club" htmlFor="shooter-club" help="One line under their name on the title page.">
            <input
              id="shooter-club"
              className={inputClass}
              value={club}
              maxLength={CLUB_MAX}
              onChange={(e) => setClub(e.target.value)}
            />
          </Field>
          {error ? <p className="px-3.5 py-2 text-sm text-led-text">{error}</p> : null}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-rule px-3.5 py-3">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={saving || !accentValid} onClick={() => void save()}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    </Sheet>
  );
}
