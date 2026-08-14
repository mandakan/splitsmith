import { type LabEvalFixture } from "@/lib/api";
import { cn } from "@/lib/utils";

export function VoterChips({
  candidate,
}: {
  candidate: LabEvalFixture["candidates"][number];
}) {
  const items: { key: string; label: string; on: boolean }[] = [
    { key: "a", label: "A", on: candidate.vote_a === 1 },
    { key: "b", label: "B", on: candidate.vote_b === 1 },
    { key: "c", label: "C", on: candidate.vote_c === 1 },
  ];
  return (
    <div className="flex gap-0.5">
      {items.map((it) => (
        <span
          key={it.key}
          className={cn(
            "rounded px-1 font-mono text-[9px]",
            it.on
              ? "bg-emerald-500/20 text-emerald-700 dark:text-emerald-300"
              : "bg-muted text-muted",
          )}
          title={`Voter ${it.label}: ${it.on ? "yes" : "no"}`}
        >
          {it.label}
        </span>
      ))}
    </div>
  );
}
