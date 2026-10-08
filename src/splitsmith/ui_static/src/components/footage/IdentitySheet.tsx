/**
 * IdentitySheet -- a shooter's identity on the Footage page (#1243): the
 * accent that tells their tile and summary apart, the logo the cards
 * draw, and a club line. Saves through PATCH /identity and the logo
 * routes; a shooter who never opens this renders with the Look's
 * defaults. Since the shooter book (spec 2026-10-08) it says where the
 * look comes from: a look from the book is edited in the book (so this
 * match keeps following it), "Only this match" keeps an edit here, and
 * "Use shooter book" drops this match's own record.
 */
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, inputClass } from "@/components/ui/Field";
import { Sheet } from "@/components/ui/Sheet";
import { ApiError, api, type IdentitySource, type ShooterListEntry } from "@/lib/api";
import { cn } from "@/lib/utils";
import { sourceLine } from "@/lib/you";

/** The shipped Look's accent series, offered as the quick picks (a
 *  shooter who picks nothing gets no accent). */
const ACCENT_SWATCHES = ["#ff2d2d", "#fbbf24", "#4ade80", "#60a5fa", "#c084fc", "#f472b6"] as const;

const HEX = /^#[0-9a-fA-F]{6}$/;

export interface IdentitySheetProps {
  open: boolean;
  onClose: () => void;
  shooter: ShooterListEntry | null;
  editDenied: boolean;
  /** After anything was saved. */
  onChanged: () => void;
}

export function IdentitySheet({ open, onClose, shooter, editDenied, onChanged }: IdentitySheetProps) {
  const [accent, setAccent] = useState<string>("");
  const [club, setClub] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  const [removeLogo, setRemoveLogo] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<IdentitySource>("none");
  const [shownLogo, setShownLogo] = useState<string | null>(null);
  const [onlyThisMatch, setOnlyThisMatch] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setAccent(shooter?.identity?.accent ?? "");
    setClub(shooter?.identity?.club ?? "");
    setShownLogo(shooter?.identity?.logo ?? null);
    setSource("none");
    setFile(null);
    setRemoveLogo(false);
    setOnlyThisMatch(false);
    setError(null);
    if (!shooter) return;
    let alive = true;
    // What a render draws for this shooter: the match's record or the book's.
    api
      .getShooterIdentityView(shooter.slug)
      .then((view) => {
        if (!alive) return;
        setSource(view.source);
        setAccent(view.identity.accent ?? "");
        setClub(view.identity.club ?? "");
        setShownLogo(view.identity.logo);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [open, shooter]);

  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  if (!shooter) return null;
  const currentLogo = shownLogo;
  const accentValid = accent === "" || HEX.test(accent);
  const shooterId = shooter.selected_shooter_id;
  // A look from the book is edited in the book, so this match keeps
  // following it; "Only this match" writes a record here instead.
  const editBook = source === "book" && shooterId != null && !onlyThisMatch;
  const scope = onlyThisMatch ? "match" : "book";

  const save = async () => {
    if (!accentValid) {
      setError("The accent is a six-digit hex colour, like #ff2d2d.");
      return;
    }
    setSaving(true);
    setError(null);
    const fields = {
      accent: accent === "" ? null : accent.toLowerCase(),
      club: club.trim() === "" ? null : club.trim(),
    };
    try {
      if (editBook && shooterId != null) {
        await api.putShooterBookEntry(shooterId, { ...fields, label: shooter.name });
        if (file) {
          await api.uploadShooterBookLogo(shooterId, file);
        } else if (removeLogo && currentLogo) {
          await api.removeShooterBookLogo(shooterId);
        }
      } else {
        await api.updateShooterIdentity(shooter.slug, { ...fields, scope });
        if (file) {
          await api.uploadShooterLogo(shooter.slug, file, scope);
        } else if (removeLogo && currentLogo) {
          await api.removeShooterLogo(shooter.slug, scope);
        }
      }
      onChanged();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save the identity.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet open={open} onClose={onClose} label={`Identity for ${shooter.name}`}>
      <div className="flex h-full flex-col">
        <div className="border-b border-rule px-3.5 py-3 text-md font-medium text-ink">{shooter.name}</div>
        <div className="flex-1 overflow-y-auto">
          <div className="flex items-start justify-between gap-3 border-b border-rule px-3.5 py-2.5">
            <p className="text-sm text-muted" data-testid="identity-source">
              {sourceLine(source, shooterId != null)}
            </p>
            {source === "match" && shooterId != null ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={editDenied || saving}
                onClick={() => {
                  setSaving(true);
                  api
                    .useShooterBook(shooter.slug)
                    .then((view) => {
                      setSource(view.source);
                      setAccent(view.identity.accent ?? "");
                      setClub(view.identity.club ?? "");
                      setShownLogo(view.identity.logo);
                      onChanged();
                    })
                    .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Could not switch."))
                    .finally(() => setSaving(false));
                }}
              >
                Use shooter book
              </Button>
            ) : null}
          </div>
          <Field label="Accent" htmlFor="identity-accent" help="Tints this shooter's summary bar and name; blank leaves the summary as the Look draws it.">
            <div className="flex flex-wrap items-center gap-2">
              {ACCENT_SWATCHES.map((hex) => (
                <button
                  key={hex}
                  type="button"
                  aria-label={`Accent ${hex}`}
                  aria-pressed={accent.toLowerCase() === hex}
                  disabled={editDenied}
                  onClick={() => setAccent(hex)}
                  className={cn(
                    "size-6 rounded-full border border-rule-strong",
                    accent.toLowerCase() === hex && "ring-2 ring-led ring-offset-2 ring-offset-surface",
                  )}
                  style={{ backgroundColor: hex }}
                />
              ))}
              <input
                id="identity-accent"
                className={cn(inputClass, "w-28 font-mono")}
                placeholder="#rrggbb"
                value={accent}
                disabled={editDenied}
                onChange={(e) => setAccent(e.target.value)}
                aria-invalid={!accentValid}
              />
            </div>
          </Field>
          <Field label="Logo" help="PNG, JPEG or WebP, at most 2 MB. Drawn top-right on the cards.">
            <div className="flex items-center gap-3">
              {preview ? (
                <img src={preview} alt="" className="size-12 rounded border border-rule object-contain" />
              ) : currentLogo && !removeLogo ? (
                <span className="numeral text-sm text-muted">{currentLogo}</span>
              ) : (
                <span className="text-sm text-muted">No logo</span>
              )}
              <input
                ref={fileInput}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                aria-label="Logo file"
                className="hidden"
                disabled={editDenied}
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setRemoveLogo(false);
                }}
              />
              <Button size="sm" variant="default" disabled={editDenied} onClick={() => fileInput.current?.click()}>
                {currentLogo || file ? "Replace" : "Choose"}
              </Button>
              {(currentLogo && !removeLogo) || file ? (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={editDenied}
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
          <Field label="Club" htmlFor="identity-club" help="One line, at most 60 characters.">
            <input
              id="identity-club"
              className={inputClass}
              value={club}
              maxLength={60}
              disabled={editDenied}
              onChange={(e) => setClub(e.target.value)}
            />
          </Field>
          {shooterId != null ? (
            <label className="flex items-center gap-2 px-3.5 py-2.5 text-md text-ink-2">
              <input
                type="checkbox"
                checked={onlyThisMatch}
                disabled={editDenied}
                onChange={(e) => setOnlyThisMatch(e.target.checked)}
              />
              Only this match
            </label>
          ) : null}
          {error ? <p className="px-3.5 py-2 text-sm text-led-text">{error}</p> : null}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-rule px-3.5 py-3">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={editDenied || saving || !accentValid} onClick={() => void save()}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    </Sheet>
  );
}
