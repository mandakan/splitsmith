/**
 * AddShooterSheet -- add a shooter to the match from the Footage page
 * (UX PR 6; the Shooters page's add section). On a scoreboard-linked
 * match the roster is the list to pick from (a pick binds the
 * competitor's identity, so their splits get an expected-rounds prior);
 * "Add by name" is the plain form. An unlinked match gets the connect
 * flow beside the name form.
 *
 * With ``linkTo`` it links that existing shooter instead: the same roster,
 * and a pick pins the competitor on them (``select-shooter``), which also
 * merges their stage times. A shooter added by name has no stage times and
 * no SSI id, and nothing else in the app can link one afterwards.
 */
import { useEffect, useMemo, useState } from "react";

import { ConnectMatchDialog } from "@/components/scoreboard/ConnectMatchDialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { Sheet } from "@/components/ui/Sheet";
import {
  ApiError,
  api,
  READ_ONLY_MIRROR_MESSAGE,
  type MatchProject,
  type ScoreboardMatchCompetitor,
  type ShooterListEntry,
} from "@/lib/api";

export interface AddShooterSheetProps {
  open: boolean;
  onClose: () => void;
  project: MatchProject | null;
  shooters: ShooterListEntry[];
  editDenied: boolean;
  /** After a shooter was added or linked, or the match was linked to the scoreboard. */
  onChanged: () => void;
  /** Link this shooter to a competitor instead of adding a new one. */
  linkTo?: ShooterListEntry | null;
}

const INPUT = "w-full rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 text-md text-ink placeholder:text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led";

export function AddShooterSheet({ open, onClose, project, shooters, editDenied, onChanged, linkTo = null }: AddShooterSheetProps) {
  const scoreboardMatchId = project?.scoreboard_match_id ?? null;
  const scoreboardContentType = project?.scoreboard_content_type ?? null;
  const isLinked = scoreboardMatchId != null && scoreboardContentType != null;
  const [manual, setManual] = useState(false);
  const [roster, setRoster] = useState<ScoreboardMatchCompetitor[] | null>(null);
  const [rosterLoading, setRosterLoading] = useState(false);
  const [rosterError, setRosterError] = useState<string | null>(null);
  const [filter, setFilter] = useState(linkTo?.name ?? "");
  // A sheet kept mounted between shooters starts from the new one's name.
  useEffect(() => setFilter(linkTo?.name ?? ""), [linkTo]);
  const [pickingId, setPickingId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connectOpen, setConnectOpen] = useState(false);

  useEffect(() => {
    if (!open || scoreboardMatchId == null || scoreboardContentType == null) {
      setRoster(null);
      return;
    }
    let alive = true;
    setRosterLoading(true);
    setRosterError(null);
    api
      .getScoreboardMatchDataUnbound(scoreboardContentType, Number(scoreboardMatchId))
      .then((match) => {
        if (alive) setRoster(match.competitors);
      })
      .catch((e) => {
        if (!alive) return;
        setRoster(null);
        setRosterError(e instanceof ApiError ? e.detail : String(e));
      })
      .finally(() => {
        if (alive) setRosterLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, scoreboardMatchId, scoreboardContentType]);

  // Competitors already claimed by a shooter never show as pickable.
  const claimed = useMemo(() => new Set(shooters.map((s) => s.selected_competitor_id).filter((id): id is number => id != null)), [shooters]);
  const available = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (roster ?? []).filter((c) => {
      if (claimed.has(c.id)) return false;
      if (!q) return true;
      return `${c.name} ${c.club ?? ""} ${c.division ?? ""}`.toLowerCase().includes(q);
    });
  }, [roster, claimed, filter]);

  async function pick(c: ScoreboardMatchCompetitor) {
    if (editDenied) {
      setError(READ_ONLY_MIRROR_MESSAGE);
      return;
    }
    setPickingId(c.id);
    setError(null);
    try {
      if (linkTo) await api.selectScoreboardShooter(linkTo.slug, c.shooterId, c.id);
      else await api.addMatchShooter({ name: c.name, division: c.division, selected_shooter_id: c.shooterId, selected_competitor_id: c.id });
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setPickingId(null);
    }
  }

  async function addByName() {
    if (editDenied || !name.trim()) return;
    setAdding(true);
    setError(null);
    try {
      await api.addMatchShooter({ name: name.trim() });
      setName("");
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setAdding(false);
    }
  }

  const showRoster = isLinked && (!manual || linkTo != null);
  const title = linkTo ? `Link ${linkTo.name} to the scoreboard` : "Add shooter";
  return (
    <Sheet open={open} onClose={onClose} label={title}>
      <div className="flex items-center gap-3 border-b border-rule px-4 py-3">
        <span className="min-w-0 flex-1 text-md font-medium text-ink">{title}</span>
        <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close">
          &#10005;
        </Button>
      </div>
      <div className="flex flex-col gap-4 overflow-y-auto px-4 py-4 text-md text-ink-2">
        {editDenied ? <p className="text-sm text-muted">{READ_ONLY_MIRROR_MESSAGE}</p> : null}
        {showRoster ? (
          <>
            <p className="text-sm text-muted">
              {linkTo
                ? `Pick ${linkTo.name} on the scoreboard: their stage times come in, so Audit can trim, and they get a look of their own.`
                : "This match is linked to the scoreboard: pick a competitor and their splits get an expected-rounds prior."}
            </p>
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by name, club or division"
              aria-label="Filter roster"
              className={INPUT}
            />
            <div className="overflow-hidden rounded-[10px] border border-rule">
              {rosterLoading ? (
                <p className="px-3 py-2.5 text-sm text-muted">Loading roster from scoreboard…</p>
              ) : rosterError ? (
                <p role="alert" className="px-3 py-2.5 text-sm text-led-text">
                  {rosterError}
                </p>
              ) : available.length === 0 ? (
                <p className="px-3 py-2.5 text-sm text-muted">{roster && roster.length > 0 ? "No one matches." : "Nobody left to add."}</p>
              ) : (
                available.slice(0, 60).map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    disabled={editDenied || pickingId != null}
                    onClick={() => void pick(c)}
                    className="flex w-full items-center gap-3 border-b border-rule px-3 py-2 text-left last:border-b-0 hover:bg-surface-2 disabled:opacity-50"
                  >
                    <span className="min-w-0 flex-1 truncate text-ink">{c.name}</span>
                    <span className="truncate text-sm text-muted">{[c.division, c.club].filter(Boolean).join(" · ")}</span>
                    {pickingId === c.id ? <span className="text-sm text-muted">{linkTo ? "Linking…" : "Adding…"}</span> : null}
                  </button>
                ))
              )}
            </div>
            {linkTo ? null : (
              <button type="button" onClick={() => setManual(true)} className="self-start text-sm text-ink-2 hover:text-ink">
                Add by name instead
              </button>
            )}
          </>
        ) : (
          <>
            {linkTo ? null : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void addByName();
              }}
              className="flex flex-col gap-2"
            >
              <Label>Name</Label>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Shooter name" aria-label="Shooter name" disabled={editDenied} className={INPUT} />
              <Button type="submit" variant="primary" disabled={editDenied || adding || !name.trim()} className="self-start">
                {adding ? "Adding…" : "Add shooter"}
              </Button>
            </form>
            )}
            {isLinked ? (
              <button type="button" onClick={() => setManual(false)} className="self-start text-sm text-ink-2 hover:text-ink">
                Pick from the scoreboard roster instead
              </button>
            ) : editDenied ? null : (
              <div className="rounded-[10px] border border-rule px-3 py-2.5 text-sm text-muted">
                Not linked to the scoreboard yet. Connect it to pull official stage times and pick shooters from the roster.{" "}
                <Button size="sm" className="ml-2" onClick={() => setConnectOpen(true)}>
                  Connect
                </Button>
              </div>
            )}
          </>
        )}
        {error ? (
          <p role="alert" className="text-sm text-led-text">
            {error}
          </p>
        ) : null}
      </div>
      {connectOpen ? (
        <ConnectMatchDialog
          shooters={shooters}
          onClose={() => setConnectOpen(false)}
          onApplied={() => {
            setConnectOpen(false);
            onChanged();
          }}
        />
      ) : null}
    </Sheet>
  );
}
