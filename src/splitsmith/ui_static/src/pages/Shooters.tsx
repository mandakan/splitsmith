/**
 * Shooters (spec 2026-10-09): everyone you have filmed, with the look their
 * videos draw. One row per SSI shooter id, you first; Edit opens the shooter
 * sheet, which writes the shooter book, so the look follows them into every
 * match. A shooter not linked to the scoreboard has no id to key a look by:
 * their row says so and cannot be edited here. Account level, both modes.
 */
import { useCallback, useEffect, useState } from "react";

import { ShooterSheet, type SheetShooter } from "@/components/shooters/ShooterSheet";
import { Avatar } from "@/components/ui/AvatarStack";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import { ApiError, api, type ShooterRosterRow } from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { canEdit, initials, rowKey, rowNote } from "@/lib/shooters";

function sheetShooter(row: ShooterRosterRow): SheetShooter | null {
  if (row.shooter_id === null) return null;
  return {
    shooterId: row.shooter_id,
    name: row.name,
    accent: row.accent,
    club: row.club,
    logoUrl: row.logo_url,
  };
}

export function Shooters() {
  const { mode } = useDeploymentMode();
  const [rows, setRows] = useState<ShooterRosterRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<SheetShooter | null>(null);

  const load = useCallback(() => {
    api
      .listShooters()
      .then((r) => {
        setRows(r.rows);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Could not read your shooters."));
  }, []);

  useEffect(load, [load]);

  const sub =
    rows === null
      ? "Loading..."
      : `${rows.length} ${rows.length === 1 ? "shooter" : "shooters"} you have filmed`;
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-7 py-5">
      <PageHeader
        title="Shooters"
        sub={sub}
        back={mode === "hosted" ? { label: "Account", to: "/account" } : { label: "Matches", to: "/pick" }}
      />
      <p className="max-w-[70ch] text-md text-ink-2">
        A shooter&apos;s logo, colour and club are drawn in every video of theirs. Set them once here and every
        match follows.
      </p>
      {error ? <p className="text-sm text-led-text">{error}</p> : null}
      {rows !== null && rows.length === 0 ? (
        <p className="text-md text-muted">No shooters yet. They appear here once a match has footage of them.</p>
      ) : null}
      {rows !== null && rows.length > 0 ? (
        <Table>
          <thead>
            <tr>
              <Th>Shooter</Th>
              <Th>Club</Th>
              <Th align="right">
                <span className="sr-only">Actions</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <Tr key={rowKey(row)}>
                <Td>
                  <div className="flex items-center gap-3">
                    <Avatar
                      initials={initials(row.name)}
                      seed={row.name}
                      size="md"
                      accent={row.accent}
                      logo={row.logo_url}
                    />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-md text-ink">
                        <span className="truncate">{row.name}</span>
                        {row.you ? <Chip tick="muted">You</Chip> : null}
                      </div>
                      <div className="text-sm text-muted">{rowNote(row)}</div>
                    </div>
                  </div>
                </Td>
                <Td>{row.club ?? <span className="text-muted">-</span>}</Td>
                <Td className="text-right">
                  {canEdit(row) ? (
                    <Button size="sm" variant="default" onClick={() => setEditing(sheetShooter(row))}>
                      Edit
                    </Button>
                  ) : null}
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      ) : null}
      <ShooterSheet open={editing !== null} onClose={() => setEditing(null)} shooter={editing} onChanged={load} />
    </div>
  );
}

export default Shooters;
