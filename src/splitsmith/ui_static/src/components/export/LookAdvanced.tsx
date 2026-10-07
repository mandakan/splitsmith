/**
 * LookAdvanced -- the way into your own Looks, inside the Look row of the
 * Look group (spec 2026-10-07 s5, #1264; the UX pass moved it up from a row
 * of its own under the transitions): a "Make your own" tile beside the Look
 * tiles, Edit on a Look of yours, and the guide.
 *
 * Making one names it and picks what it starts from before anything is
 * written; Create copies the chosen Look (``looks new --from`` locally, a
 * manifest on its shipped base hosted) under that label and opens the
 * editor on the copy, where Discard removes it again until it is saved. A
 * shipped Look is never edited in place, so a shipped name always means the
 * same thing.
 */
import { Plus } from "lucide-react";
import { useState } from "react";

import { GUIDE_URL, LookEditor } from "@/components/export/LookEditor";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import { Sheet } from "@/components/ui/Sheet";
import { NewChip } from "@/components/whatsNew/WhatsNew";
import { ApiError, api, type LookInfo } from "@/lib/api";
import { lookNameFor, MAX_LABEL } from "@/lib/lookEditor";
import { DEFAULT_LOOK, previewSrc } from "@/lib/looks";
import { refreshLooks } from "@/lib/useLooks";
import { dismissNewChip } from "@/lib/useWhatsNew";
import { cn } from "@/lib/utils";

export interface LookAdvancedProps {
  looks: LookInfo[];
  /** The Look the form has chosen. */
  look: string;
  onChooseLook: (name: string) => void;
  slug: string;
  stageNumber: number;
  hosted: boolean;
  busy: boolean;
  /** The shooter's Identity sheet on the Footage page, for the palette. */
  identityHref?: string;
}

export function LookAdvanced({
  looks,
  look,
  onChooseLook,
  slug,
  stageNumber,
  hosted,
  busy,
  identityHref,
}: LookAdvancedProps) {
  const [editing, setEditing] = useState<{ name: string; isNew: boolean } | null>(null);
  const [starting, setStarting] = useState(false);
  const current = looks.find((l) => l.name === look);
  const editable = current?.editable === true;

  return (
    <>
      <button
        type="button"
        onClick={() => {
          dismissNewChip("look-editor");
          setStarting(true);
        }}
        disabled={busy}
        aria-label="Make your own Look: your colours, fonts and logo"
        className={cn(
          "flex w-40 cursor-pointer flex-col gap-1.5 rounded-md border border-dashed border-rule-strong p-1.5 text-left",
          "transition-colors hover:border-ink-2 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led disabled:opacity-50",
        )}
      >
        <span className="flex aspect-video w-full items-center justify-center rounded-sm bg-surface-3">
          <Plus className="size-6 text-ink-2" aria-hidden />
        </span>
        <span className="flex items-center gap-1.5 text-sm text-ink">
          Make your own <NewChip feature="look-editor" />
        </span>
        <span className="text-sm text-muted">Your colours, fonts and logo</span>
      </button>
      <div className="flex flex-col items-start gap-2 sm:pt-2">
        {editable ? (
          <Button
            variant="default"
            size="sm"
            onClick={() => {
              dismissNewChip("look-editor");
              setEditing({ name: look, isNew: false });
            }}
            disabled={busy}
          >
            Edit this Look
          </Button>
        ) : null}
        <a
          className="text-sm text-led-text underline-offset-4 hover:underline"
          href={GUIDE_URL}
          target="_blank"
          rel="noreferrer"
        >
          How Looks work
        </a>
      </div>
      {starting ? (
        <StartSheet
          looks={looks}
          from={look}
          onClose={() => setStarting(false)}
          onCreated={(name) => {
            setStarting(false);
            onChooseLook(name);
            setEditing({ name, isNew: true });
          }}
        />
      ) : null}
      {editing ? (
        <LookEditor
          open
          onClose={() => setEditing(null)}
          name={editing.name}
          isNew={editing.isNew}
          identityHref={identityHref}
          info={looks.find((l) => l.name === editing.name)}
          slug={slug}
          stageNumber={stageNumber}
          hosted={hosted}
          onDeleted={() => onChooseLook(editing.isNew && look !== editing.name ? look : DEFAULT_LOOK)}
        />
      ) : null}
    </>
  );
}

/** The first step of making a Look: its name and what it starts from.
 *  Nothing is written until Create. */
function StartSheet({
  looks,
  from,
  onClose,
  onCreated,
}: {
  looks: LookInfo[];
  from: string;
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const [label, setLabel] = useState("My Look");
  const [source, setSource] = useState(looks.some((l) => l.name === from) ? from : DEFAULT_LOOK);
  const [working, setWorking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const trimmed = label.trim();
  const tooLong = trimmed.length > MAX_LABEL;

  const create = async () => {
    const name = lookNameFor(
      trimmed,
      looks.map((l) => l.name),
    );
    setWorking(true);
    setProblem(null);
    try {
      await api.duplicateLook(name, source, trimmed || undefined);
      await refreshLooks();
      onCreated(name);
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : "The Look could not be made.");
    } finally {
      setWorking(false);
    }
  };

  return (
    <Sheet open onClose={onClose} label="Make your own Look" className="md:max-w-[640px]">
      <div className="border-b border-rule px-4 py-3">
        <h2 className="text-base text-ink">Make your own Look</h2>
        <p className="text-sm text-muted">
          Your colours, card styles and fonts, on every export you choose it for.
        </p>
      </div>
      <div className="flex flex-col gap-4 overflow-y-auto p-4">
        <label className="flex flex-col gap-1.5">
          <Label>Name</Label>
          <input
            aria-label="Name"
            className={cn(inputClass, "max-w-[320px]")}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            autoFocus
          />
          {tooLong ? (
            <span role="alert" className="text-sm text-destructive">
              Keep the name under {MAX_LABEL} characters.
            </span>
          ) : null}
        </label>
        <div className="flex flex-col gap-1.5">
          <Label>Start from</Label>
          <div role="radiogroup" aria-label="Start from" className="flex flex-wrap gap-2">
            {looks.map((l) => {
              const checked = l.name === source;
              return (
                <button
                  key={l.name}
                  type="button"
                  role="radio"
                  aria-checked={checked}
                  aria-label={l.label || l.name}
                  onClick={() => setSource(l.name)}
                  className={cn(
                    "flex w-36 cursor-pointer flex-col gap-1.5 rounded-md border p-1.5 text-left transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
                    checked ? "border-ink bg-surface-2" : "border-rule-strong hover:border-ink-2",
                  )}
                >
                  {l.preview ? (
                    <img
                      src={previewSrc(l.preview) ?? undefined}
                      alt=""
                      className="aspect-video w-full rounded-sm bg-surface-3 object-cover"
                    />
                  ) : (
                    <span className="aspect-video w-full rounded-sm bg-surface-3" />
                  )}
                  <span className="text-sm text-ink-2">{l.label || l.name}</span>
                </button>
              );
            })}
          </div>
          <p className="text-sm text-muted">
            The copy starts with its colours and card styles; change any of them next.
          </p>
        </div>
        {problem ? (
          <p role="alert" className="text-sm text-destructive">
            {problem}
          </p>
        ) : null}
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-rule px-4 py-3">
        <Button variant="default" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" onClick={() => void create()} disabled={working || tooLong}>
          {working ? "Making…" : "Create"}
        </Button>
      </div>
    </Sheet>
  );
}
