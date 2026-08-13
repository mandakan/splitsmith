/**
 * CommentPanel - public, timestamped comments on a stage's video.
 *
 * Mounted on ResultsStage (both the owner and the anonymous share
 * surface). The thread loads on mount; a failed load degrades to an
 * inline retry rather than an error page - the player above it must
 * keep working regardless of whether the thread loaded.
 *
 * Each comment stores both an `anchor_t` (seconds after the beep) and
 * an optional shot id. The id is a label; `t` is the truth - if the
 * shot it names no longer resolves against the current shot table (a
 * re-detect moved or removed it), the row falls back to a time label
 * instead of mislabeling a different shot. See lib/commentAnchor.ts.
 *
 * The compose box renders when `canComment`; a Delete button renders on
 * every comment when `canModerate`, and on the caller's own comments
 * always (`c.mine`). Both flags come from the caller - see
 * ResultsStage's `canComment`/`canModerate` pair for how one
 * `comment_write` capability splits into the two. The server refuses a
 * POST the token's scope doesn't grant with the same uniform **404** it
 * returns for an unknown token, not a 403: the whole share surface is
 * deliberately undiscoverable by probing.
 */
import { Clock, Loader2, Target } from "lucide-react";
import { useEffect, useState } from "react";

import { ambiguousCodes } from "@/lib/authorAmbiguity";
import { api, apiErrorText, type Comment, type CoachShot, type CommentAuthor } from "@/lib/api";
import { snapToShot } from "@/lib/commentAnchor";
import { cn } from "@/lib/utils";

interface CommentPanelProps {
  slug: string;
  stage: number;
  shots: readonly CoachShot[];
  /** Seconds - the stage's beep position in the served clip's
   *  coordinate system, same as ResultsStage's `coach.beep_time`. */
  beepTime: number;
  /** Seconds - the video element's current playback position in the
   *  same coordinate system, same as ResultsStage's `currentTime`. */
  currentTime: number;
  /** Render the compose box. True only where a POST can succeed: a
   *  share mount whose token carries `comment_write`. */
  canComment: boolean;
  /** Render Delete on every comment, not just the caller's own. True on
   *  the owner's own mount, where `comment_write` means "may moderate".
   *  Optional so an existing caller keeps today's behaviour. */
  canModerate?: boolean;
  /** Clip-absolute seconds, the same coordinate system SplitsList's
   *  `onSeek` already uses for shot rows. */
  onSeek: (t: number) => void;
  /** Optional: fires with every comment's `anchor_t` (seconds after
   *  beep) whenever the thread changes, so the page can render scrub-bar
   *  pins without a second fetch of the same thread. */
  onAnchorsChange?: (anchors: number[]) => void;
}

const LOAD_FAILED_FALLBACK = "Could not load comments - check the connection and retry.";
const POST_FAILED_FALLBACK = "Could not post the comment - check the connection and retry.";

/** Format an ISO timestamp as "DD Mon YYYY". Mirrors DesktopTokensSection's
 *  formatTokenDate and ShareDialog's formatShareDate -- kept local rather
 *  than shared since none of the three otherwise have any coupling. Guards
 *  against an unparseable value: this runs inside render, and the ISO
 *  string comes from the author-detail endpoint, not a value this
 *  component controls. */
function formatCommentDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const day = String(d.getUTCDate()).padStart(2, "0");
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  return `${day} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** `Shot <n>` when the anchor's shot id still resolves against the
 *  current shot table; otherwise a plain time pin. A distinct icon
 *  accompanies the text in both cases so the anchor kind is never
 *  color-only (WCAG 1.4.1, same stance as the moment marker). */
function anchorLabel(
  c: Comment,
  shots: readonly CoachShot[],
): { Icon: typeof Target; text: string } {
  if (c.anchor_kind === "shot" && c.anchor_shot_id != null) {
    const shot = shots.find((s) => s.id === c.anchor_shot_id);
    if (shot) return { Icon: Target, text: `Shot ${shot.shot_number}` };
  }
  return { Icon: Clock, text: `${c.anchor_t.toFixed(2)} s` };
}

export function CommentPanel({
  slug,
  stage,
  shots,
  beepTime,
  currentTime,
  canComment,
  canModerate = false,
  onSeek,
  onAnchorsChange,
}: CommentPanelProps) {
  const [comments, setComments] = useState<Comment[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [body, setBody] = useState("");
  const [posting, setPosting] = useState(false);
  const [postError, setPostError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // Keyed by comment id, not author_code: an author with several
  // comments in the thread gets a trigger per comment, and only the one
  // the reader clicked should open (#867 final review, M3).
  const [openCommentId, setOpenCommentId] = useState<string | null>(null);
  const [authors, setAuthors] = useState<CommentAuthor[] | null>(null);

  // Recomputed per render off `comments` rather than memoized: the
  // thread is bounded by the per-stage comment cap, so this is cheap,
  // and memoizing it would just be a second place to get the deps wrong.
  const ambiguous = ambiguousCodes(comments ?? []);

  // Single choke point for updating the thread: keeps onAnchorsChange in
  // lockstep with `comments` without a second effect (whose dependency
  // on the callback identity would either loop or need suppressing).
  function commitComments(next: Comment[]) {
    setComments(next);
    onAnchorsChange?.(next.map((c) => c.anchor_t));
  }

  useEffect(() => {
    let alive = true;
    setLoadError(null);
    (async () => {
      try {
        const res = await api.listStageComments(slug, stage);
        if (!alive) return;
        commitComments(res.comments);
      } catch (e) {
        if (!alive) return;
        setLoadError(apiErrorText(e, LOAD_FAILED_FALLBACK));
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- commitComments closes over comments/onAnchorsChange but only slug/stage/attempt should re-run the fetch
  }, [slug, stage, attempt]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = body.trim();
    if (!trimmed || posting) return;
    const t = Math.round((currentTime - beepTime) * 100) / 100;
    const { anchor_kind, anchor_shot_id } = snapToShot(t, shots);
    setPosting(true);
    setPostError(null);
    try {
      const created = await api.createStageComment(slug, stage, {
        body: trimmed,
        anchor_t: t,
        anchor_kind,
        anchor_shot_id,
      });
      // Append, not prepend: the server returns the thread oldest-first,
      // so prepending showed a different order than the next reload
      // would (final review, M3).
      commitComments([...(comments ?? []), created]);
      // A comment can post successfully even after the initial thread
      // load failed (the compose box doesn't wait on that fetch) - clear
      // the retry banner so the freshly-posted comment isn't hidden
      // behind it. The render branch checks loadError before comments.
      setLoadError(null);
      setBody("");
    } catch (e) {
      setPostError(apiErrorText(e, POST_FAILED_FALLBACK));
    } finally {
      setPosting(false);
    }
  }

  // Owner-only, and only when they actually ask: the endpoint is a
  // match-wide aggregate, so fetching it on mount would cost every
  // reader a query for a panel most of them never open.
  async function openAuthorDetail(commentId: string) {
    setOpenCommentId((current) => (current === commentId ? null : commentId));
    if (authors !== null) return;
    try {
      const resp = await api.listCommentAuthors();
      setAuthors(resp.authors);
    } catch {
      // Detail is an enrichment - a failed fetch leaves the panel
      // showing the code alone, which is still the disambiguator.
      setAuthors([]);
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id);
    try {
      await api.deleteStageComment(slug, stage, id);
      commitComments((comments ?? []).filter((c) => c.id !== id));
    } catch {
      // A stale-optimism delete failure just leaves the row in place -
      // the user can retry the same button, no separate error surface.
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <section className="overflow-hidden rounded-2xl border border-rule-strong bg-surface">
      <div className="border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-4 py-3 font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
        Comments
        {comments ? (
          <span className="ml-2 font-mono text-[0.625rem] font-medium tracking-[0.06em] text-muted">
            {comments.length} total
          </span>
        ) : null}
      </div>

      {loadError ? (
        <div className="flex flex-col items-start gap-2 px-4 py-4">
          <p role="alert" className="text-sm text-led-text">
            {loadError}
          </p>
          <button
            type="button"
            onClick={() => setAttempt((n) => n + 1)}
            className="inline-flex min-h-9 items-center rounded-md border border-rule-strong bg-surface-2 px-3 font-display text-xs font-bold uppercase tracking-[0.08em] text-ink transition-colors hover:bg-surface-3"
          >
            Retry
          </button>
        </div>
      ) : comments == null ? (
        <div className="flex items-center gap-2 px-4 py-4 text-sm text-muted">
          <Loader2 className="size-4 animate-spin" /> Loading comments...
        </div>
      ) : comments.length === 0 ? (
        <p className="px-4 py-4 text-sm text-muted">No comments yet.</p>
      ) : (
        <ul className="divide-y divide-rule">
          {comments.map((c) => {
            const { Icon, text } = anchorLabel(c, shots);
            return (
              // flex-col, not the row's old flex: the author line and the
              // detail trigger below are now siblings of the seek button,
              // not children of it. A <button> cannot legally contain
              // another <button> (React warns via console.error at
              // runtime), and the moderator-only detail trigger added
              // here is the second button on the row. The seek affordance
              // stays on the anchor chip and the body text, inside their
              // own button underneath.
              <li key={c.id} className="flex flex-col">
                <div className="flex flex-wrap items-center gap-2 px-4 pt-3">
                  <span
                    className="font-mono text-xs font-bold uppercase tracking-[0.06em] text-ink"
                    data-author-code={c.author_code}
                    title={`${c.author_handle} - author ${c.author_code}`}
                  >
                    {c.author_handle}
                  </span>
                  {/* The code goes visible only when another author in
                      this thread posts under the same name. Always
                      showing it would put a code on every line of a
                      thread that is usually one or two people; never
                      showing it would leave a spoofed name reading as
                      authoritative. A moderator gets the code either way,
                      as the detail trigger's own label below -- so this
                      plain span only covers the non-moderator case, and
                      never renders alongside the button (that would put
                      the same code on the line twice, #867 final review
                      M1). */}
                  {!canModerate && ambiguous.has(c.author_code) ? (
                    <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                      {c.author_code}
                    </span>
                  ) : null}
                  {canModerate ? (
                    <button
                      type="button"
                      aria-label={`Author detail for ${c.author_handle}`}
                      aria-expanded={openCommentId === c.id}
                      aria-controls={`author-detail-${c.id}`}
                      onClick={() => void openAuthorDetail(c.id)}
                      className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted transition-colors hover:text-ink"
                    >
                      {c.author_code}
                    </button>
                  ) : null}
                </div>
                {openCommentId === c.id ? (
                  <span
                    id={`author-detail-${c.id}`}
                    className="mx-4 mt-1 block rounded border border-rule bg-surface-2 p-2 text-[0.6875rem] text-muted"
                  >
                    {(() => {
                      const detail = authors?.find((a) => a.author_code === c.author_code);
                      if (!detail) return "Author detail unavailable.";
                      return `${detail.author_kind === "account" ? "Account" : "Pseudonym"} - ${detail.comment_count} comments since ${formatCommentDate(detail.first_comment_at)} - posted as ${detail.handles.join(", ")}`;
                    })()}
                  </span>
                ) : null}
                <div className="flex items-stretch">
                  <button
                    type="button"
                    onClick={() => onSeek(beepTime + c.anchor_t)}
                    className="min-h-11 flex-1 px-4 pb-3 pt-1 text-left transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-inset"
                  >
                    <span className="inline-flex items-center gap-1 rounded border border-rule px-1.5 py-0.5 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                      <Icon aria-hidden className="size-3" />
                      {text}
                    </span>
                    <p className="mt-1 text-sm text-ink-2">{c.body}</p>
                  </button>
                  {c.mine || canModerate ? (
                    <button
                      type="button"
                      onClick={() => void handleDelete(c.id)}
                      disabled={deletingId === c.id}
                      className="shrink-0 self-center px-3 font-mono text-[0.625rem] font-bold uppercase tracking-[0.06em] text-muted transition-colors hover:text-led-text disabled:opacity-50"
                    >
                      Delete
                    </button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {canComment ? (
        <form
          onSubmit={(e) => void handleSubmit(e)}
          className="flex flex-col gap-2 border-t border-rule px-4 py-3"
        >
          <label htmlFor="comment-panel-body" className="sr-only">
            Add a comment
          </label>
          <textarea
            id="comment-panel-body"
            aria-label="Add a comment"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Add a comment..."
            rows={2}
            className="w-full resize-none rounded-md border border-rule bg-surface-2 px-3 py-2 text-sm text-ink outline-none focus:border-led focus:shadow-[0_0_0_3px_var(--color-led-tint)]"
          />
          {postError ? (
            <p role="alert" className="text-xs text-led-text">
              {postError}
            </p>
          ) : null}
          <button
            type="submit"
            disabled={!body.trim() || posting}
            className={cn(
              "inline-flex min-h-9 items-center self-end rounded-md border border-rule-strong bg-surface-2 px-4 font-display text-xs font-bold uppercase tracking-[0.08em] text-ink transition-colors hover:bg-surface-3",
              (!body.trim() || posting) && "opacity-50",
            )}
          >
            {posting ? "Posting..." : "Post"}
          </button>
        </form>
      ) : null}
    </section>
  );
}
