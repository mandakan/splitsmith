"""Auto-sync scheduling core (spec 2026-09-27 s2). Pure, fake clock."""

from __future__ import annotations

from splitsmith.sync.auto import AutoSyncCore

E = {"m1"}


def test_push_waits_for_the_quiet_period() -> None:
    core = AutoSyncCore()
    core.mark_dirty("m1", now=100.0)
    assert core.pick(130.0, enabled=E, busy=(), sync_active=False) is None
    core.mark_dirty("m1", now=130.0)  # another write resets the clock
    assert core.pick(170.0, enabled=E, busy=(), sync_active=False) is None
    assert core.pick(175.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_remote_change_is_pulled_without_waiting() -> None:
    core = AutoSyncCore()
    core.on_poll_ok(10.0, server={"m1": (3, 9)}, local={"m1": (3, 8)})
    assert core.pick(10.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_equal_fingerprints_do_nothing() -> None:
    core = AutoSyncCore()
    core.on_poll_ok(10.0, server={"m1": (3, 9)}, local={"m1": (3, 9)})
    assert core.pick(10.0, enabled=E, busy=(), sync_active=False) is None


def test_busy_match_disabled_match_and_active_sync_defer() -> None:
    core = AutoSyncCore()
    core.mark_pull_due("m1")
    assert core.pick(0.0, enabled=E, busy={"m1"}, sync_active=False) is None
    assert core.pick(0.0, enabled=set(), busy=(), sync_active=False) is None
    assert core.pick(0.0, enabled=E, busy=(), sync_active=True) is None
    assert core.pick(0.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_success_clears_only_what_it_covered() -> None:
    core = AutoSyncCore()
    core.mark_dirty("m1", now=0.0)
    core.on_sync_started("m1", now=50.0)
    core.mark_dirty("m1", now=60.0)  # a write during the sync
    core.on_sync_done("m1", now=70.0, ok=True)
    assert core.pick(80.0, enabled=E, busy=(), sync_active=False) is None
    assert core.pick(105.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_failure_backs_off_exponentially_to_the_cap() -> None:
    """Each failure parks the match; a local write releases it, but never
    sooner than the backoff."""
    core = AutoSyncCore()
    core.mark_pull_due("m1")
    waits = []
    now = 0.0
    for _ in range(6):
        core.on_sync_started("m1", now=now)
        core.on_sync_done("m1", now=now, ok=False)
        core.mark_dirty("m1", now=now - core.QUIET_S)
        t = now
        while core.pick(t, enabled=E, busy=(), sync_active=False) is None:
            t += 1.0
        waits.append(t - now)
        now = t
    assert waits == [60.0, 120.0, 240.0, 480.0, 900.0, 900.0]


def _parked_after_failure() -> AutoSyncCore:
    core = AutoSyncCore()
    core.on_poll_ok(0.0, server={"m1": (3, 9)}, local={"m1": (3, 8)})
    core.on_sync_started("m1", now=0.0)
    core.on_sync_done("m1", now=10.0, ok=False)
    return core


def test_a_failed_sync_waits_for_a_change_instead_of_retrying() -> None:
    """#1070: a sync that can never succeed used to fail again every
    backoff period, each failure a new job in the strip."""
    core = _parked_after_failure()
    assert core.waiting_for_change("m1")
    # Hours later, polls unchanged: still quiet.
    for t in range(60, 36_000, 60):
        core.on_poll_ok(float(t), server={"m1": (3, 9)}, local={"m1": (3, 8)})
        assert core.pick(float(t), enabled=E, busy=(), sync_active=False) is None


def test_a_local_write_releases_a_parked_match() -> None:
    core = _parked_after_failure()
    core.mark_dirty("m1", now=1000.0)
    assert not core.waiting_for_change("m1")
    assert core.pick(1000.0 + core.QUIET_S, enabled=E, busy=(), sync_active=False) == "m1"


def test_a_remote_change_releases_a_parked_match() -> None:
    core = _parked_after_failure()
    core.on_poll_ok(1000.0, server={"m1": (3, 10)}, local={"m1": (3, 8)})
    assert core.pick(1000.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_hosted_coming_back_releases_a_parked_match() -> None:
    """A sync that failed because hosted was down is retried once a poll
    gets through again."""
    core = _parked_after_failure()
    core.on_poll_error(500.0, "offline", auth=False)
    core.on_poll_ok(600.0, server={"m1": (3, 9)}, local={"m1": (3, 8)})
    assert core.pick(600.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_a_successful_manual_sync_releases_a_parked_match() -> None:
    core = _parked_after_failure()
    core.on_sync_done("m1", now=1000.0, ok=True, started_at=990.0)
    assert not core.waiting_for_change("m1")
    core.mark_dirty("m1", now=1100.0)
    assert core.pick(1100.0 + core.QUIET_S, enabled=E, busy=(), sync_active=False) == "m1"


def test_settled_fingerprint_does_not_retrigger() -> None:
    """A doc deleted hosted-side leaves the pair different forever. After a
    successful sync the scheduler remembers that server fingerprint and
    waits for it to move."""
    core = AutoSyncCore()
    core.on_poll_ok(0.0, server={"m1": (2, 5)}, local={"m1": (3, 6)})
    core.on_sync_started("m1", now=1.0)
    core.on_sync_done("m1", now=2.0, ok=True)
    core.on_poll_ok(60.0, server={"m1": (2, 5)}, local={"m1": (3, 6)})
    assert core.pick(60.0, enabled=E, busy=(), sync_active=False) is None
    core.on_poll_ok(120.0, server={"m1": (2, 6)}, local={"m1": (3, 6)})
    assert core.pick(120.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_poll_interval_relaxes_when_idle_and_tightens_on_activity() -> None:
    core = AutoSyncCore()
    core.on_poll_ok(0.0, server={}, local={})
    assert core.next_poll_at == 60.0
    core.on_poll_ok(1900.0, server={}, local={})
    assert core.next_poll_at == 2200.0
    core.mark_dirty("m1", now=2000.0)
    core.on_poll_ok(2010.0, server={}, local={})
    assert core.next_poll_at == 2070.0


def test_auth_error_blocks_until_cleared_and_transport_errors_back_off() -> None:
    core = AutoSyncCore()
    core.on_poll_error(0.0, "could not reach the hosted server", auth=False)
    assert core.paused_reason == "could not reach the hosted server"
    assert core.next_poll_at == 60.0
    core.on_poll_error(60.0, "could not reach the hosted server", auth=False)
    assert core.next_poll_at == 180.0
    core.mark_pull_due("m1")
    core.on_poll_error(200.0, "sign in again", auth=True)
    assert core.auth_blocked()
    assert core.pick(1000.0, enabled=E, busy=(), sync_active=False) is None
    core.clear_auth_block()
    assert core.pick(1000.0, enabled=E, busy=(), sync_active=False) == "m1"
    core.on_poll_ok(1000.0, server={}, local={})
    assert core.paused_reason is None


def test_no_sync_is_attempted_while_the_poll_cannot_reach_hosted() -> None:
    """Offline, a sync can only fail, and every failed auto_sync lands in
    the jobs strip for the user to acknowledge. Hold off until a poll
    gets through."""
    core = AutoSyncCore()
    core.mark_pull_due("m1")
    core.mark_dirty("m1", now=0.0)
    core.on_poll_error(100.0, "offline", auth=False)
    assert core.pick(200.0, enabled=E, busy=(), sync_active=False) is None
    core.on_poll_ok(300.0, server={}, local={})
    assert core.pick(300.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_remote_change_seen_during_a_sync_is_not_settled_by_it() -> None:
    """A poll that lands after the sync's pull phase (media uploads can run
    for minutes) sees a phone change that sync never pulled. Settling it at
    the end of that sync would drop the change until some other write."""
    core = AutoSyncCore()
    core.on_poll_ok(0.0, server={"m1": (3, 3)}, local={"m1": (3, 3)})
    core.mark_dirty("m1", now=0.0)
    core.on_sync_started("m1", now=100.0)
    core.on_poll_ok(120.0, server={"m1": (3, 4)}, local={"m1": (3, 3)})
    core.on_sync_done("m1", now=130.0, ok=True)
    core.on_poll_ok(200.0, server={"m1": (3, 4)}, local={"m1": (3, 3)})
    assert core.pick(200.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_sync_done_uses_a_given_start_when_none_was_recorded() -> None:
    """A manual sync never went through on_sync_started; its job's own start
    time decides which writes it covered."""
    core = AutoSyncCore()
    core.mark_dirty("m1", now=110.0)  # an edit while the manual sync ran
    core.on_sync_done("m1", now=130.0, ok=True, started_at=100.0)
    assert core.pick(160.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_a_running_render_holds_every_match() -> None:
    """A render on any match holds every automatic sync: a sync's web-clip
    backfill encodes on the same CPU the render needs."""
    core = AutoSyncCore()
    core.mark_pull_due("m1")
    assert core.pick(0.0, enabled=E, busy=(), sync_active=False, render_active=True) is None
    assert core.pick(0.0, enabled=E, busy=(), sync_active=False, render_active=False) == "m1"
