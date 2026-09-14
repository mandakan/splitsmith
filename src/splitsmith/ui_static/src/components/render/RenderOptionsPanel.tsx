/**
 * RenderOptionsPanel -- the generated-card and stage-summary rows of a
 * rendered export (#973, #972; #974 items C and D).
 *
 * Props-driven Field rows for the Export page's Options section: it owns
 * no state, knows no endpoint, and never submits. `surface` decides what
 * it offers -- the summary hold belongs to the single-shooter export
 * only; the grid's hold is #705's and lives on the page -- and
 * `outputFormat` decides which rows are live: the match cards and the
 * summary exist only in the rendered MP4, the per-stage card reaches
 * FCPXML too (a Basic Title) and only the FCP 7 XML has nowhere to put
 * it. A row the format cannot honour disables itself and says so in its
 * help line rather than letting a user set a knob the server would then
 * report as ignored.
 *
 * Visual budget: Field rows, Segmented for every closed choice, no
 * colour but the focus ring, no primary action (the page owns it).
 */
import * as React from "react";

import { Field, inputClass } from "@/components/ui/Field";
import { Segmented } from "@/components/ui/Segmented";
import { cn } from "@/lib/utils";
import {
  cardsSupported,
  stageCardsSupported,
  type OutputFormat,
  type RenderOptions,
  type StageCardStyle,
} from "@/lib/renderOptions";

export type RenderSurface = "single" | "grid";

export interface RenderOptionsPanelProps {
  value: RenderOptions;
  onChange: (next: RenderOptions) => void;
  /** Which export mounts the panel; the grid has no summary hold. */
  surface: RenderSurface;
  /** The export's chosen format. The grid is always an MP4; pass
   *  `"mp4"` there. */
  outputFormat: OutputFormat | undefined;
  /** Disables every control while a render is queued. */
  busy?: boolean;
}

type TitlePageChoice = "none" | "open" | "both" | "close";

const TITLE_PAGE_CHOICES: ReadonlyArray<{ value: TitlePageChoice; label: string }> = [
  { value: "none", label: "None" },
  { value: "open", label: "Opening" },
  { value: "both", label: "Opening + closing" },
  { value: "close", label: "Closing" },
];

const STAGE_CARD_STYLES: ReadonlyArray<{ value: StageCardStyle; label: string }> = [
  { value: "none", label: "None" },
  { value: "slate", label: "Slate" },
  { value: "lower-third", label: "Lower third" },
];

const MP4_ONLY = "Renders in MP4 output only.";

function titleChoice(value: RenderOptions): TitlePageChoice {
  if (value.titlePage) return value.closingCard ? "both" : "open";
  return value.closingCard ? "close" : "none";
}

export function RenderOptionsPanel({ value, onChange, surface, outputFormat, busy = false }: RenderOptionsPanelProps) {
  const matchCards = cardsSupported(outputFormat);
  const stageCards = stageCardsSupported(outputFormat);
  const id = React.useId();
  const set = <K extends keyof RenderOptions>(key: K, next: RenderOptions[K]) => onChange({ ...value, [key]: next });
  const titleOn = value.titlePage || value.closingCard;

  return (
    <>
      <Field
        label="Title page"
        help={
          !matchCards
            ? MP4_ONLY
            : titleOn
              ? "Match name and date, with the info line under it."
              : "A card with the match name and date before the first stage, after the last, or both."
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          <Segmented<TitlePageChoice>
            label="Title page"
            value={titleChoice(value)}
            disabled={busy || !matchCards}
            onChange={(choice) =>
              onChange({
                ...value,
                titlePage: choice === "open" || choice === "both",
                closingCard: choice === "close" || choice === "both",
              })
            }
            options={TITLE_PAGE_CHOICES}
          />
          {matchCards && titleOn ? (
            <Seconds
              id={`${id}-title-seconds`}
              label="Title page seconds"
              value={value.titlePageDurationSeconds}
              min={0.5}
              disabled={busy}
              onChange={(n) => set("titlePageDurationSeconds", n)}
            />
          ) : null}
        </div>
        {matchCards && titleOn ? (
          <input
            id={`${id}-title-info`}
            aria-label="Title page info line"
            type="text"
            className={cn(inputClass, "mt-2 max-w-md")}
            placeholder="Division, level, anything under the match name"
            value={value.titleInfo}
            disabled={busy}
            onChange={(e) => set("titleInfo", e.target.value)}
          />
        ) : null}
      </Field>

      <Field
        label="Stage cards"
        help={
          !stageCards
            ? "FCP 7 XML has no title track; pick FCPXML or MP4 for a card per stage."
            : value.stageCardStyle === "lower-third"
              ? "Stage name and round count over the stage's first seconds."
              : value.stageCardStyle === "slate"
                ? "Stage name and round count on its own card before each stage."
                : "A card per stage: a slate before it, or a lower third over its start."
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          <Segmented<StageCardStyle>
            label="Stage card style"
            value={value.stageCardStyle}
            disabled={busy || !stageCards}
            onChange={(style) => set("stageCardStyle", style)}
            options={STAGE_CARD_STYLES}
          />
          {stageCards && value.stageCardStyle !== "none" ? (
            <Seconds
              id={`${id}-stage-seconds`}
              label="Stage card seconds"
              value={value.stageCardDurationSeconds}
              min={0.5}
              disabled={busy}
              onChange={(n) => set("stageCardDurationSeconds", n)}
            />
          ) : null}
        </div>
      </Field>

      {surface === "single" ? (
        <Field
          label="Stage summary"
          htmlFor={`${id}-summary-seconds`}
          help={
            !matchCards
              ? MP4_ONLY
              : value.summaryHoldSeconds > 0
                ? "Held after each stage: name, scoring, splits."
                : "Seconds to hold each stage's summary after its last shot; 0 is off."
          }
        >
          <Seconds
            id={`${id}-summary-seconds`}
            label="Summary hold seconds"
            value={value.summaryHoldSeconds}
            min={0}
            disabled={busy || !matchCards}
            onChange={(n) => set("summaryHoldSeconds", n)}
          />
        </Field>
      ) : null}
    </>
  );
}

/** A seconds input; a blank or unparsable field is NaN on the value and
 *  the mapper clamps it to the floor at submit time. */
export function Seconds({
  id,
  label,
  value,
  min,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  min: number;
  disabled: boolean;
  onChange: (n: number) => void;
}) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted">
      <input
        id={id}
        aria-label={label}
        type="number"
        inputMode="decimal"
        min={min}
        max={30}
        step={0.5}
        className={cn(inputClass, "w-20 font-mono text-sm")}
        value={Number.isFinite(value) ? value : ""}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value.trim() === "" ? Number.NaN : Number(e.target.value))}
      />
      s
    </span>
  );
}
