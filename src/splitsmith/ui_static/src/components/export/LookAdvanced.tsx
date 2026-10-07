/**
 * LookAdvanced -- the Look group's last row (spec 2026-10-07 s5, #1264):
 * duplicate any Look into one of your own, edit your own, and the guide.
 * A shipped Look is never edited in place, so a shipped name always means
 * the same thing.
 */
import { useState } from "react";

import { GUIDE_URL, LookEditor } from "@/components/export/LookEditor";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { NewChip } from "@/components/whatsNew/WhatsNew";
import { ApiError, api, type LookInfo } from "@/lib/api";
import { duplicateName } from "@/lib/lookEditor";
import { DEFAULT_LOOK } from "@/lib/looks";
import { refreshLooks } from "@/lib/useLooks";
import { dismissNewChip } from "@/lib/useWhatsNew";

export interface LookAdvancedProps {
  looks: LookInfo[];
  /** The Look the form has chosen. */
  look: string;
  onChooseLook: (name: string) => void;
  slug: string;
  stageNumber: number;
  hosted: boolean;
  busy: boolean;
}

export function LookAdvanced({
  looks,
  look,
  onChooseLook,
  slug,
  stageNumber,
  hosted,
  busy,
}: LookAdvancedProps) {
  const [editing, setEditing] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const current = looks.find((l) => l.name === look);
  const editable = current?.editable === true;

  const duplicate = async () => {
    dismissNewChip("look-editor");
    const name = duplicateName(
      look,
      looks.map((l) => l.name),
    );
    setWorking(true);
    setProblem(null);
    try {
      await api.duplicateLook(name, look);
      await refreshLooks();
      onChooseLook(name);
      setEditing(name);
    } catch (e) {
      setProblem(
        e instanceof ApiError ? e.message : "The Look could not be copied.",
      );
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="flex flex-col gap-2 border-t border-rule px-3.5 py-3">
      <div className="flex items-center gap-2">
        <Label>Advanced</Label>
        <NewChip feature="look-editor" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="default"
          size="sm"
          onClick={() => void duplicate()}
          disabled={busy || working}
        >
          Duplicate Look…
        </Button>
        {editable ? (
          <Button
            variant="default"
            size="sm"
            onClick={() => {
              dismissNewChip("look-editor");
              setEditing(look);
            }}
            disabled={busy}
          >
            Edit Look…
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
      {problem ? (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      ) : null}
      {editing ? (
        <LookEditor
          open
          onClose={() => setEditing(null)}
          name={editing}
          info={looks.find((l) => l.name === editing)}
          slug={slug}
          stageNumber={stageNumber}
          hosted={hosted}
          onDeleted={() => onChooseLook(DEFAULT_LOOK)}
        />
      ) : null}
    </div>
  );
}
