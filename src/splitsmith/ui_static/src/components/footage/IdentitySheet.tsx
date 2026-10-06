/**
 * IdentitySheet -- a shooter's identity on the Footage page (#1243): the
 * accent that tells their tile and summary apart, the logo the cards
 * draw, and a club line. Saves through PATCH /identity and the logo
 * routes; a shooter who never opens this renders with the Look's
 * defaults.
 */
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, inputClass } from "@/components/ui/Field";
import { Sheet } from "@/components/ui/Sheet";
import { ApiError, api, type ShooterListEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

/** The shipped Look's accent series: what a shooter without an accent
 *  of their own is told apart by, offered as the quick picks. */
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
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setAccent(shooter?.identity?.accent ?? "");
    setClub(shooter?.identity?.club ?? "");
    setFile(null);
    setRemoveLogo(false);
    setError(null);
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
  const currentLogo = shooter.identity?.logo ?? null;
  const accentValid = accent === "" || HEX.test(accent);

  const save = async () => {
    if (!accentValid) {
      setError("The accent is a six-digit hex colour, like #ff2d2d.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.updateShooterIdentity(shooter.slug, {
        accent: accent === "" ? null : accent.toLowerCase(),
        club: club.trim() === "" ? null : club.trim(),
      });
      if (file) {
        await api.uploadShooterLogo(shooter.slug, file);
      } else if (removeLogo && currentLogo) {
        await api.removeShooterLogo(shooter.slug);
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
          <Field label="Accent" htmlFor="identity-accent" help="Tints this shooter's summary and name; blank uses the Look's own colour for their slot.">
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
