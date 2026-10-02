/**
 * CamOptionsPanel -- the camera rows of the single-shooter match export
 * (#193; #974 item A; 2026-10-02): which camera is the picture, a second
 * one inset in a corner, and, on an editing timeline, the other angles
 * carried switched off.
 *
 * Props-driven like RenderOptionsPanel: no state of its own, no endpoint,
 * no button; the Export page feeds it the choices from the project
 * (``cameraChoices``) and the saved default's name. It renders nothing
 * when the shooter has no synced secondary on the selected stages -- a
 * control for cameras the shooter does not have is a question with no
 * answer, not a disabled row.
 */
import { Field } from "@/components/ui/Field";
import { Segmented } from "@/components/ui/Segmented";
import type {
  CameraChoice,
  CamOptions,
  InsetCorner,
  InsetSize,
} from "@/lib/camOptions";

export interface CamOptionsPanelProps {
  value: CamOptions;
  onChange: (next: CamOptions) => void;
  /** `syncedSecondaryCount(...)` for the stages the export will take. */
  secondaryCount: number;
  /** `cameraChoices(...)` for the same stages. */
  choices: CameraChoice[];
  /** What "Saved default" lands on, e.g. "Handheld". */
  savedLabel: string;
  /** An editing timeline (FCPXML, FCP 7): offers the other angles. */
  editing: boolean;
  /** The compare grid: each shooter's main camera is their saved one, so
   *  the row says so instead of asking; the inset is one choice for all. */
  grid?: boolean;
  busy?: boolean;
}

const NONE = "none";

const CORNERS: ReadonlyArray<{ value: InsetCorner; label: string }> = [
  { value: "top-left", label: "Top left" },
  { value: "top-right", label: "Top right" },
  { value: "bottom-left", label: "Bottom left" },
  { value: "bottom-right", label: "Bottom right" },
];

const SIZES: ReadonlyArray<{ value: InsetSize; label: string }> = [
  { value: "small", label: "Small" },
  { value: "medium", label: "Medium" },
  { value: "large", label: "Large" },
];

export function CamOptionsPanel({
  value,
  onChange,
  secondaryCount,
  choices,
  savedLabel,
  editing,
  grid = false,
  busy = false,
}: CamOptionsPanelProps) {
  if (secondaryCount <= 0) return null;
  const cams = grid
    ? `${secondaryCount} ${secondaryCount === 1 ? "shooter" : "shooters"} with more than one camera`
    : secondaryCount === 1
      ? "1 synced camera"
      : `${secondaryCount} synced cameras`;
  const mainOptions = [
    { value: "default", label: "Saved default" },
    ...choices,
  ];
  const insetOptions = [{ value: NONE, label: "None" }, ...choices];
  const mainRow = grid ? (
    <Field label="Main camera" hint={<span className="numeral">{cams}</span>}>
      <p className="pt-1.5 text-md text-ink-2">
        Each shooter's saved camera, set per shooter in the share dialog or
        Compare.
      </p>
    </Field>
  ) : (
    <Field
      label="Main camera"
      hint={<span className="numeral">{cams}</span>}
      help={
        value.mainCamera === "default"
          ? `The shooter's saved camera (${savedLabel}), set in the share dialog or Compare. A stage without it shows the primary; the sound is the main camera's.`
          : "A stage without this camera shows the primary; the sound is the main camera's."
      }
    >
      <Segmented<string>
        label="Main camera"
        value={value.mainCamera}
        disabled={busy}
        onChange={(v) => onChange({ ...value, mainCamera: v })}
        options={mainOptions}
      />
    </Field>
  );
  return (
    <>
      {mainRow}
      <Field
        label="Inset"
        help={
          value.insetCamera === null
            ? grid
              ? "A second camera small in a corner of each shooter's tile."
              : "A second camera small in a corner over the main one."
            : grid
              ? "In every tile whose shooter has that camera on the stage, never over their main one."
              : "Shown on the stages that have it, and never when it is the main camera."
        }
      >
        <div className="flex flex-col gap-2">
          <Segmented<string>
            label="Inset camera"
            value={value.insetCamera ?? NONE}
            disabled={busy}
            onChange={(v) =>
              onChange({ ...value, insetCamera: v === NONE ? null : v })
            }
            options={insetOptions}
          />
          {value.insetCamera !== null ? (
            <div className="flex flex-wrap gap-2">
              <Segmented<InsetCorner>
                label="Inset corner"
                value={value.insetCorner}
                disabled={busy}
                onChange={(v) => onChange({ ...value, insetCorner: v })}
                options={CORNERS}
              />
              <Segmented<InsetSize>
                label="Inset size"
                value={value.insetSize}
                disabled={busy}
                onChange={(v) => onChange({ ...value, insetSize: v })}
                options={SIZES}
              />
            </div>
          ) : null}
        </div>
      </Field>
      {editing && !grid ? (
        <Field
          label="Other angles"
          help={
            value.includeSecondaries
              ? "Every other camera on its own track, switched off: turn one on in the editor to use it."
              : "Only the main camera and the inset go into the timeline."
          }
        >
          <Segmented<"on" | "off">
            label="Other angles"
            value={value.includeSecondaries ? "on" : "off"}
            disabled={busy}
            onChange={(v) =>
              onChange({ ...value, includeSecondaries: v === "on" })
            }
            options={[
              { value: "on", label: "Include for editing" },
              { value: "off", label: "Leave out" },
            ]}
          />
        </Field>
      ) : null}
    </>
  );
}
