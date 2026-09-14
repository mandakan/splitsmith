/** A select whose options are checked against the state it drives.
 *
 *  `T` is inferred from `value`, so passing a union-typed state (an
 *  `OverlayCodec`, an output format) makes the compiler reject any
 *  `options` entry that isn't a member of it. Callers therefore need no
 *  cast in `onChange` -- which is the point: a cast at the call site
 *  launders whatever the select emits straight into typed state, and
 *  that is exactly how the Export page came to offer two overlay codecs
 *  the backend rejects with a 422 (issue #761).
 *
 *  The one `as T` lives here, and it is the only place it can be made
 *  safely: a `<select>` can emit nothing but the `value`s rendered
 *  below, and every one of those is a `T`.
 *
 *  `options` is `NoInfer<T>` deliberately. Without it `T` widens to
 *  absorb whatever the options carry and the check is gone.
 */
import { ChevronDown } from "lucide-react";

import { inputClass } from "@/components/ui/Field";
import { cn } from "@/lib/utils";

export function SelectField<T extends string>({
  label,
  value,
  onChange,
  options,
  disabled = false,
  title,
  className,
}: {
  /** Accessible name; not rendered (the Field row carries the visible label). */
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: readonly { value: NoInfer<T>; label: string }[];
  disabled?: boolean;
  /** Tooltip/aria reason shown while disabled (e.g. #756's
   *  `READ_ONLY_MIRROR_MESSAGE`). Ignored while enabled. */
  title?: string;
  className?: string;
}) {
  return (
    <span className={cn("relative inline-block", className)}>
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        disabled={disabled}
        title={disabled ? title : undefined}
        className={cn(inputClass, "appearance-none pr-8 text-sm disabled:cursor-not-allowed")}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted"
      />
    </span>
  );
}
