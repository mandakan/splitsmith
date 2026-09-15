/** Name a preset (Save as...) or rename one. A `Sheet` with one field. */
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, inputClass } from "@/components/ui/Field";
import { Sheet } from "@/components/ui/Sheet";
import { cn } from "@/lib/utils";

export interface SavePresetSheetProps {
  open: boolean;
  title: string;
  initialName: string;
  onClose: () => void;
  onSubmit: (name: string) => void;
}

export function SavePresetSheet({ open, title, initialName, onClose, onSubmit }: SavePresetSheetProps) {
  const [name, setName] = useState(initialName);
  const trimmed = name.trim();
  return (
    <Sheet open={open} onClose={onClose} label="Save preset">
      <form
        className="flex flex-col gap-3 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (trimmed) onSubmit(trimmed);
        }}
      >
        <h2 className="text-base text-ink">{title}</h2>
        <Field label="Name" htmlFor="preset-name">
          <input
            id="preset-name"
            autoFocus
            maxLength={60}
            className={cn(inputClass, "w-full")}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={!trimmed}>
            Save
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
