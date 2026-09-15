/**
 * A seconds input for the Look gallery's parameters. A blank or
 * unparsable field is NaN on the value and the mapper clamps it to the
 * floor at submit time. Moved out of the retired render panel.
 */
import { inputClass } from "@/components/ui/Field";
import { cn } from "@/lib/utils";

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
