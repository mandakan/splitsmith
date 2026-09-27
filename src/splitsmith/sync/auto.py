"""Desktop auto-sync scheduling core (spec 2026-09-27 s2).

Pure and clock-injected: the FastAPI driver (``ui/auto_sync.py``) feeds
it poll results, local writes and job outcomes, and asks ``pick`` which
match to sync next. Nothing here touches the network, the filesystem or
the job registry.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

#: ``(doc_count, version_sum)``, plus the identity digest when hosted
#: sends one (#1072). Compared for equality only.
Fingerprint = tuple[int, int] | tuple[int, int, str]


@dataclass
class _MatchState:
    pull_due: bool = False
    #: When the remote change behind ``pull_due`` was observed; a sync that
    #: started later pulled it, one that started earlier may not have.
    pull_seen_at: float = 0.0
    push_due_at: float | None = None
    sync_started_at: float | None = None
    retry_at: float = 0.0
    failures: int = 0
    pending_fp: Fingerprint | None = None
    settled_fp: Fingerprint | None = None
    #: The last hosted fingerprint a poll reported for this match.
    server_fp: Fingerprint | None = None
    #: A failed sync parks the match (#1070): no automatic retry until
    #: something changes, so a sync that can never succeed fails once in
    #: the jobs strip instead of every backoff period. ``parked_fp`` is
    #: the hosted fingerprint at the failure; a poll that moves it counts
    #: as a change.
    parked: bool = False
    parked_fp: Fingerprint | None = None


class AutoSyncCore:
    POLL_ACTIVE_S = 60.0
    POLL_IDLE_S = 300.0
    IDLE_AFTER_S = 1800.0
    QUIET_S = 45.0
    BACKOFF_MIN_S = 60.0
    BACKOFF_MAX_S = 900.0

    def __init__(self) -> None:
        self._matches: dict[str, _MatchState] = {}
        self._last_activity = 0.0
        self._poll_failures = 0
        self._auth_blocked = False
        self.paused_reason: str | None = None
        self.next_poll_at = 0.0

    def _m(self, match_id: str) -> _MatchState:
        return self._matches.setdefault(match_id, _MatchState())

    def _backoff(self, failures: int) -> float:
        return min(self.BACKOFF_MIN_S * 2 ** (failures - 1), self.BACKOFF_MAX_S)

    def mark_dirty(self, match_id: str, now: float) -> None:
        st = self._m(match_id)
        st.push_due_at = now
        st.parked = False  # a local write is a change worth retrying for
        self._last_activity = now

    def waiting_for_change(self, match_id: str) -> bool:
        """Whether the match is parked after a failed sync."""
        st = self._matches.get(match_id)
        return st is not None and st.parked

    def mark_pull_due(self, match_id: str) -> None:
        self._m(match_id).pull_due = True

    def on_poll_ok(
        self, now: float, server: Mapping[str, Fingerprint], local: Mapping[str, Fingerprint]
    ) -> None:
        """Compare the hosted fingerprints with the local ones. A pair that
        still differs after a successful sync (a doc deleted hosted-side,
        say) is remembered as settled and ignored until it moves."""
        recovered = self._poll_failures > 0
        self._poll_failures = 0
        self.paused_reason = None
        for match_id, local_fp in local.items():
            server_fp = server.get(match_id)
            if server_fp is None:
                continue
            st = self._m(match_id)
            st.server_fp = server_fp
            # Hosted reachable again, or hosted moved since the failure:
            # either may be what the parked sync was waiting for.
            if st.parked and (recovered or server_fp != st.parked_fp):
                st.parked = False
            if server_fp == local_fp:
                st.settled_fp = None
                continue
            if server_fp == st.settled_fp:
                continue
            st.pull_due = True
            st.pull_seen_at = now
            st.pending_fp = server_fp
            self._last_activity = now
        idle = now - self._last_activity >= self.IDLE_AFTER_S
        self.next_poll_at = now + (self.POLL_IDLE_S if idle else self.POLL_ACTIVE_S)

    def on_poll_error(self, now: float, reason: str, *, auth: bool) -> None:
        self.paused_reason = reason
        if auth:
            self._auth_blocked = True
            return
        self._poll_failures += 1
        self.next_poll_at = now + self._backoff(self._poll_failures)

    def auth_blocked(self) -> bool:
        return self._auth_blocked

    def clear_auth_block(self) -> None:
        """A new token is a fresh start: the next tick polls at once."""
        self._auth_blocked = False
        self._poll_failures = 0
        self.paused_reason = None
        self.next_poll_at = 0.0

    def on_sync_started(self, match_id: str, now: float) -> None:
        self._m(match_id).sync_started_at = now

    def on_sync_done(self, match_id: str, now: float, *, ok: bool, started_at: float | None = None) -> None:
        """``started_at`` is the job's own start, for a sync this core did
        not start (a manual one); without either, ``now`` stands in."""
        st = self._m(match_id)
        if st.sync_started_at is not None:
            started = st.sync_started_at
        elif started_at is not None:
            started = started_at
        else:
            started = now
        st.sync_started_at = None
        if not ok:
            st.failures += 1
            # The backoff still floors a retry once a change releases it,
            # so a burst of edits over a broken sync cannot hammer hosted.
            st.retry_at = now + self._backoff(st.failures)
            st.parked = True
            st.parked_fp = st.server_fp
            return
        st.failures = 0
        st.retry_at = 0.0
        st.parked = False
        # Only what was observed before the sync started is covered by it:
        # a poll that lands after its pull phase saw a change it never pulled.
        if st.pull_seen_at <= started:
            st.pull_due = False
            if st.pending_fp is not None:
                st.settled_fp = st.pending_fp
                st.pending_fp = None
        # A write that landed after the sync started is not covered by it.
        if st.push_due_at is not None and st.push_due_at <= started:
            st.push_due_at = None

    def pick(
        self,
        now: float,
        *,
        enabled: Collection[str],
        busy: Collection[str],
        sync_active: bool,
        render_active: bool = False,
    ) -> str | None:
        """The match to sync now, or None. Pulls first, then quiet pushes.
        Nothing while the last poll failed: a sync would only fail too.
        Nothing while a render runs on any match: a sync opens with the
        web-clip backfill, an encode per trim on the render's CPU."""
        if self._auth_blocked or sync_active or render_active or self._poll_failures:
            return None
        ready: list[tuple[int, str]] = []
        for match_id in sorted(enabled):
            st = self._matches.get(match_id)
            if st is None or st.parked or match_id in busy or now < st.retry_at:
                continue
            if st.pull_due:
                ready.append((0, match_id))
            elif st.push_due_at is not None and now - st.push_due_at >= self.QUIET_S:
                ready.append((1, match_id))
        return min(ready)[1] if ready else None
