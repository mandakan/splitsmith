/**
 * RenderOptionsPanel -- the generated-card and stage-summary controls of a
 * rendered MP4 (#973, #972; #974 items C and D).
 *
 * Self-contained and props-driven so the rebuilt Export page (PR 8) and
 * the Compare page (PR 7) mount it where they see fit: it owns no state,
 * knows no endpoint, and never submits. `surface` decides what it offers
 * -- the summary hold belongs to the single-shooter export only; the
 * grid's hold is #705's -- and `outputFormat` decides whether the
 * controls are live at all: cards exist only in the rendered MP4, so on
 * an XML format the panel disables itself and says so in one neutral
 * line rather than letting a user set knobs the server would then
 * report as ignored.
 *
 * Visual budget: hairline rows, Label for the three section names, no
 * colour but the focus ring, no primary action (the page owns it).
 */
import * as React from "react";

import { Label } from "@/components/ui/Label";
import { cn } from "@/lib/utils";
import {
  cardsSupported,
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
  /** The export's chosen format. Anything but `"mp4"` disables the
   *  panel. The grid is always an MP4; pass `"mp4"` there. */
  outputFormat: OutputFormat | undefined;
  /** Disables every control while a render is queued. */
  busy?: boolean;
  className?: string;
}

const STAGE_CARD_STYLES: ReadonlyArray<{ value: StageCardStyle; label: string }> = [
  { value: "none", label: "None" },
  { value: "slate", label: "Slate before the stage" },
  { value: "lower-third", label: "Lower-third over the start" },
];

const FIELD = "rounded border border-rule bg-bg px-3 py-1.5 text-sm text-ink disabled:opacity-50";
const NUMBER = cn(FIELD, "w-24 tabular-nums");

export function RenderOptionsPanel({
  value,
  onChange,
  surface,
  outputFormat,
  busy = false,
  className,
}: RenderOptionsPanelProps) {
  const supported = cardsSupported(outputFormat);
  const disabled = busy || !supported;
  const id = React.useId();
  const set = <K extends keyof RenderOptions>(key: K, next: RenderOptions[K]) =>
    onChange({ ...value, [key]: next });
  const number = (raw: string): number => (raw.trim() === "" ? Number.NaN : Number(raw));

  return (
    <section
      aria-labelledby={`${id}-heading`}
      data-testid="render-options"
      className={cn("flex flex-col", className)}
    >
      <div className="flex items-baseline justify-between border-b border-rule py-2">
        <Label id={`${id}-heading`} tone="ink">
          Rendered video
        </Label>
        {!supported ? (
          <span className="text-sm text-muted" data-testid="render-options-note">
            Cards render in MP4 output only
          </span>
        ) : null}
      </div>

      <Row label="Title page">
        <div className="flex flex-wrap items-center gap-3">
          <Check
            id={`${id}-title-page`}
            label="Open with the match name"
            checked={value.titlePage}
            disabled={disabled}
            onChange={(checked) => set("titlePage", checked)}
          />
          <Check
            id={`${id}-closing`}
            label="Close with the same card"
            checked={value.closingCard}
            disabled={disabled}
            onChange={(checked) => set("closingCard", checked)}
          />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <input
            id={`${id}-title-info`}
            aria-label="Title page info line"
            type="text"
            className={cn(FIELD, "min-w-0 flex-1")}
            placeholder="Division, level, anything under the match name"
            value={value.titleInfo}
            disabled={disabled || !(value.titlePage || value.closingCard)}
            onChange={(e) => set("titleInfo", e.target.value)}
          />
          <Seconds
            id={`${id}-title-seconds`}
            label="Title page seconds"
            value={value.titlePageDurationSeconds}
            min={0.5}
            disabled={disabled || !(value.titlePage || value.closingCard)}
            onChange={(n) => set("titlePageDurationSeconds", n)}
          />
        </div>
      </Row>

      <Row label="Stage cards">
        <div className="flex flex-wrap items-center gap-3">
          <select
            id={`${id}-stage-card`}
            aria-label="Stage card style"
            className={FIELD}
            value={value.stageCardStyle}
            disabled={disabled}
            onChange={(e) => set("stageCardStyle", e.target.value as StageCardStyle)}
          >
            {STAGE_CARD_STYLES.map((style) => (
              <option key={style.value} value={style.value}>
                {style.label}
              </option>
            ))}
          </select>
          <Seconds
            id={`${id}-stage-seconds`}
            label="Stage card seconds"
            value={value.stageCardDurationSeconds}
            min={0.5}
            disabled={disabled || value.stageCardStyle === "none"}
            onChange={(n) => set("stageCardDurationSeconds", n)}
          />
        </div>
      </Row>

      {surface === "single" ? (
        <Row label="Stage summary">
          <div className="flex flex-wrap items-center gap-3">
            <Seconds
              id={`${id}-summary-seconds`}
              label="Summary hold seconds"
              value={value.summaryHoldSeconds}
              min={0}
              disabled={disabled}
              onChange={(n) => set("summaryHoldSeconds", n)}
            />
            <span className="text-sm text-muted">
              {value.summaryHoldSeconds > 0
                ? "Held after each stage: name, scoring, splits"
                : "0 is off"}
            </span>
          </div>
        </Row>
      ) : null}
    </section>
  );

  function Seconds({
    id: fieldId,
    label,
    value: seconds,
    min,
    disabled: off,
    onChange: change,
  }: {
    id: string;
    label: string;
    value: number;
    min: number;
    disabled: boolean;
    onChange: (n: number) => void;
  }) {
    return (
      <span className="inline-flex items-center gap-2">
        <input
          id={fieldId}
          aria-label={label}
          type="number"
          inputMode="decimal"
          min={min}
          max={30}
          step={0.5}
          className={NUMBER}
          value={Number.isFinite(seconds) ? seconds : ""}
          disabled={off}
          onChange={(e) => change(number(e.target.value))}
        />
        <span className="text-sm text-muted">s</span>
      </span>
    );
  }
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-2 border-b border-rule py-3 sm:grid-cols-[9rem_1fr] sm:gap-4">
      <Label>{label}</Label>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function Check({
  id,
  label,
  checked,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label htmlFor={id} className={cn("inline-flex items-center gap-2 text-sm", disabled && "opacity-50")}>
      <input
        id={id}
        type="checkbox"
        className="size-4 accent-[color:var(--color-ink)]"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}
