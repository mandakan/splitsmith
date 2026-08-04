import { describe, expect, it } from "vitest";

import type {
  ShooterStageEditResult,
  StageEditSummary,
} from "@/lib/api";
import { generalErrors } from "./EditStagesDrawer";

function shooter(
  slug: string,
  overrides: Partial<ShooterStageEditResult> = {},
): ShooterStageEditResult {
  return {
    slug,
    videos_unassigned: 0,
    audit_docs_deleted: 0,
    files_deleted: 0,
    objects_deleted: 0,
    error: null,
    saved: true,
    ...overrides,
  };
}

function summary(
  shooters: ShooterStageEditResult[],
  errors: string[],
): StageEditSummary {
  return {
    removed: [3],
    added: [],
    renamed: [],
    jobs_cancelled: 0,
    shooters,
    errors,
  };
}

describe("generalErrors", () => {
  it("drops the outer per-shooter failure the shooter row already shows", () => {
    // Server contract: ``result.error = str(exc)`` and
    // ``summary.errors.append(f"{slug}: {exc}")``. The two differ, so a
    // string-equality dedup rendered this shooter twice.
    const s = summary(
      [shooter("anna", { saved: false, error: "boom" })],
      ["anna: boom"],
    );
    expect(generalErrors(s)).toEqual([]);
  });

  it("drops the per-stage cleanup failure end to end", () => {
    // ``result.error = f"stage {n}: {exc}"`` and
    // ``summary.errors.append(f"{slug}: {detail}")``.
    const s = summary(
      [shooter("erik", { saved: true, error: "stage 3: state store down" })],
      ["erik: stage 3: state store down"],
    );
    expect(generalErrors(s)).toEqual([]);
  });

  it("keeps a purge failure whose shooter row carries a different error", () => {
    // Best-effort purge failures are tied to no ``result.error``. Here the
    // shooter also hit a per-stage failure, so the row is non-null but
    // names something else -- the purge line must still be shown.
    const s = summary(
      [shooter("anna", { saved: true, error: "stage 3: audit gone" })],
      ["anna: delete /x/y: denied", "anna: stage 3: audit gone"],
    );
    expect(generalErrors(s)).toEqual(["anna: delete /x/y: denied"]);
  });

  it("keeps a purge failure when the shooter row has no error at all", () => {
    // The documented ``error: null`` + orphaned-cache-files case. A
    // slug-based dedup would hide this line entirely, and it is the only
    // place the failure is ever reported.
    const s = summary([shooter("anna")], ["anna: delete /x/y: denied"]);
    expect(generalErrors(s)).toEqual(["anna: delete /x/y: denied"]);
  });

  it("keeps errors belonging to no shooter at all", () => {
    const s = summary(
      [shooter("anna", { saved: false, error: "boom" })],
      ["cancel jobs: worker unreachable", "anna: boom"],
    );
    expect(generalErrors(s)).toEqual(["cancel jobs: worker unreachable"]);
  });

  it("does not treat a lookalike from another shooter as a duplicate", () => {
    // ``erik: boom`` is not accounted for by anna's row.
    const s = summary(
      [
        shooter("anna", { saved: false, error: "boom" }),
        shooter("erik"),
      ],
      ["anna: boom", "erik: boom"],
    );
    expect(generalErrors(s)).toEqual(["erik: boom"]);
  });
});
