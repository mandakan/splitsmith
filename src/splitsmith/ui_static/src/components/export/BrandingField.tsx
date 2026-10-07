/**
 * BrandingField -- the Branding row under Export, Details (the branding
 * work). Your brand (a club, a personal brand, a sponsor) lives in the Look
 * and is set in the Look editor; this row says so. The event's own logo,
 * the centrepiece of the title page and the closing card above the match
 * name, is the match's and rarely used, so it is one compact optional control.
 */
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/Field";
import { ApiError, api, eventLogoUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

export function BrandingField({ busy }: { busy: boolean }) {
  const inputId = useId();
  // Bumped on every change so the thumbnail reloads. The thumbnail hides
  // itself when the image does not answer (no event logo is a 404).
  const [version, setVersion] = useState(0);
  const [shown, setShown] = useState(true);
  const [working, setWorking] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [hasLogo, setHasLogo] = useState(false);

  async function upload(file: File | undefined) {
    if (!file) return;
    setWorking(true);
    setRefused(null);
    try {
      const r = await api.uploadEventLogo(file);
      setHasLogo(r.event_logo !== null);
      setShown(true);
      setVersion((v) => v + 1);
    } catch (err) {
      setRefused(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setWorking(false);
    }
  }

  async function remove() {
    setWorking(true);
    setRefused(null);
    try {
      await api.removeEventLogo();
      setHasLogo(false);
      setShown(false);
    } catch (err) {
      setRefused(
        err instanceof ApiError
          ? err.detail
          : "The event logo could not be removed.",
      );
    } finally {
      setWorking(false);
    }
  }

  return (
    <Field
      label="Branding"
      error={refused}
      help="Your brand (a club, a personal brand, a sponsor) is part of the Look: set it in the Look editor under Card styles, Your brand."
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm text-muted">
          Event logo (optional), above the match name:
        </span>
        {shown ? (
          <img
            key={version}
            src={eventLogoUrl(version)}
            alt="Event logo"
            className="h-8 w-auto max-w-[96px] rounded-sm object-contain"
            onLoad={() => setHasLogo(true)}
            onError={() => setShown(false)}
          />
        ) : null}
        <label
          htmlFor={inputId}
          className={cn(
            "cursor-pointer rounded-md border border-rule-strong px-2.5 py-1 text-sm text-ink hover:border-ink-2",
            (busy || working) && "pointer-events-none opacity-60",
          )}
        >
          {hasLogo ? "Replace" : "Add"}
          <input
            id={inputId}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            aria-label="Event logo file"
            className="sr-only"
            disabled={busy || working}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              void upload(file);
            }}
          />
        </label>
        {hasLogo ? (
          <Button
            variant="ghost"
            size="sm"
            aria-label="Remove event logo"
            onClick={() => void remove()}
            disabled={busy || working}
          >
            Remove
          </Button>
        ) : null}
      </div>
    </Field>
  );
}
