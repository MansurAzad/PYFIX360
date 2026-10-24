"""The queue and the host that consumes it — the two halves of headless running.

``job_service`` is the only channel between the process that asks for work and the
process that does it, so these tests are about what one process may believe about
another: who owns a row, when a row counts as abandoned, and what an answer typed
into a dialog actually promises.

``background_service`` is driven with an injected clock, sleeper and step list, so
the whole loop runs without sleeping, without Windows, and without a GUI thread.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from pyfix360.db import session_scope
from pyfix360.db.models import BackgroundJobRow, ProjectRow
from pyfix360.models import AccessLevel, JobStatus, utcnow
from pyfix360.services import background_service, job_service, settings_service
from pyfix360.services.autopilot_service import Step


@pytest.fixture()
def scope(data_dir):
    """The test database, as the factory every service here expects."""
    return session_scope


@pytest.fixture()
def project(scope) -> int:
    with scope() as s:
        row = ProjectRow(name="demo", root_path="C:/nowhere")
        s.add(row)
        s.flush()
        return row.id


def _free() -> Step:
    """A step with no capability, built fresh every time.

    A step carries its own status, so one shared instance would report what the
    earlier test did with it rather than what this one is doing.
    """
    return Step(label="do the thing", capability="", run=lambda: "it did it")


def _enqueue(scope, project_id, **kwargs):
    with scope() as s:
        return job_service.enqueue(s, project_id, **kwargs)


def _row(scope, job_id: int) -> dict:
    """The row as another process would read it back."""
    with scope() as s:
        row = s.get(BackgroundJobRow, job_id)
        return {"status": row.status, "detail": row.detail, "error": row.error,
                "steps": [dict(x) for x in (row.steps or [])], "pid": row.pid,
                "host": row.host, "cancel": row.cancel_requested,
                "resume": row.resume_action, "confirmed": row.confirmed,
                "updated_at": row.updated_at}


def _set_status(scope, job_id: int, status: JobStatus) -> None:
    """Write a state only a host normally writes, without running a host."""
    with scope() as s:
        s.get(BackgroundJobRow, job_id).status = status.value


def _strand(scope, job_id: int, *, pid: int, silence: float) -> None:
    """Leave a row looking like a host stopped reporting ``silence`` seconds ago.

    Moving the stamp is the only honest way to make a row stale: the reaper's
    clock is real, so a test that wanted staleness would otherwise have to sleep.
    """
    with scope() as s:
        row = s.get(BackgroundJobRow, job_id)
        row.status = JobStatus.RUNNING.value
        row.pid = pid
        row.host = "ghost"
        row.started_at = utcnow() - timedelta(seconds=silence)
        row.updated_at = utcnow() - timedelta(seconds=silence)


def _host(scope, steps, **kwargs) -> background_service.Background:
    """A host that never sleeps and runs exactly the steps the test hands it."""
    kwargs.setdefault("poll_s", 0.01)
    kwargs.setdefault("stale_s", 60.0)
    kwargs.setdefault("sleeper", lambda _s: None)
    kwargs.setdefault("pid", 4242)
    kwargs.setdefault("host", "testbox")
    return background_service.Background(session_factory=scope,
                                         steps_for=lambda _p: list(steps), **kwargs)


def _policy(scope, capability: str, level: AccessLevel) -> None:
    with scope() as s:
        st = settings_service.get_settings(s)
        setattr(st, capability, level)
        settings_service.save_settings(s, st)


# --------------------------------------------------------------------------------------
# the queue: who owns a row
# --------------------------------------------------------------------------------------


def test_queueing_a_project_that_does_not_exist_queues_nothing(scope):
    with pytest.raises(ValueError, match="does not exist"):
        _enqueue(scope, 999, confirmed=True)


def test_a_claim_stamps_the_host_that_owns_the_row(scope, project):
    job = _enqueue(scope, project, confirmed=True)
    with scope() as s:
        claimed = job_service.claim(s, pid=4242, host="testbox")
    assert claimed.id == job.id
    row = _row(scope, job.id)
    assert row["status"] == "running" and row["pid"] == 4242
    assert row["host"] == "testbox"


def test_two_hosts_cannot_both_claim_one_job(scope, project):
    """The second update matches no row, so a job is worked exactly once."""
    _enqueue(scope, project, confirmed=True)
    with scope() as s:
        first = job_service.claim(s, pid=1, host="a")
        second = job_service.claim(s, pid=2, host="b")
    assert first is not None and second is None


def test_claims_are_taken_oldest_first(scope, project):
    older = _enqueue(scope, project, confirmed=True)
    _enqueue(scope, project, confirmed=True)
    with scope() as s:
        assert job_service.claim(s).id == older.id


def test_a_kind_no_host_can_run_is_failed_not_left_hanging(scope, project):
    """A row nobody has a runner for must not look alive forever."""
    job = _enqueue(scope, project, kind="teleport", confirmed=True)
    host = _host(scope, [_free()])
    host.poll()
    assert host.job is None                    # not held — it never started
    row = _row(scope, job.id)
    assert row["status"] == "failed"
    assert "no runner for 'teleport'" in row["error"]


# --------------------------------------------------------------------------------------
# the queue: answers, cancels, and the reaper
# --------------------------------------------------------------------------------------


def test_only_a_job_parked_at_a_gate_can_be_answered(scope, project):
    """A stale click must not pre-authorise a future gate."""
    job = _enqueue(scope, project, confirmed=True)
    with scope() as s, pytest.raises(ValueError, match="not waiting for approval"):
        job_service.answer(s, job.id, "approve")


def test_a_parked_job_holds_one_answer_at_a_time(scope, project):
    job = _enqueue(scope, project, confirmed=True)
    _set_status(scope, job.id, JobStatus.WAITING_APPROVAL)
    with scope() as s:
        job_service.answer(s, job.id, "approve")
    with scope() as s, pytest.raises(ValueError, match="unanswered 'approve'"):
        job_service.answer(s, job.id, "skip")


def test_an_answer_is_read_once_by_the_host(scope, project):
    job = _enqueue(scope, project, confirmed=True)
    _set_status(scope, job.id, JobStatus.WAITING_APPROVAL)
    with scope() as s:
        job_service.answer(s, job.id, "skip")
    with scope() as s:
        assert job_service.take_signals(s, job.id) == (False, "skip")
        assert job_service.take_signals(s, job.id) == (False, "")


def test_a_queued_job_is_cancelled_but_a_live_one_is_only_asked(scope, project):
    """An unclaimed row ends outright; a claimed one cannot be yanked mid-step."""
    first = _enqueue(scope, project, confirmed=True)     # the older: gets claimed
    second = _enqueue(scope, project, confirmed=True)    # stays queued
    with scope() as s:
        assert job_service.claim(s, pid=1, host="a").id == first.id
    with scope() as s:
        job_service.request_cancel(s, second.id)
    with scope() as s:
        job_service.request_cancel(s, first.id)

    assert _row(scope, second.id)["status"] == "cancelled"
    row = _row(scope, first.id)
    assert row["status"] == "running" and row["cancel"] is True


def test_the_reaper_needs_silence_and_a_dead_pid_together(scope, project):
    """Either proof alone would be wrong.

    A host grinding through a forty-second test suite is silent but alive; a dead
    host that only just stamped its row looks fresh. Reaping on one signal either
    abandons live work or leaves a dead one claimed.
    """
    stale_but_alive = _enqueue(scope, project, confirmed=True)
    dead_but_fresh = _enqueue(scope, project, confirmed=True)
    stale_and_dead = _enqueue(scope, project, confirmed=True)
    _strand(scope, stale_but_alive.id, pid=7, silence=600.0)
    _strand(scope, dead_but_fresh.id, pid=8, silence=0.0)
    _strand(scope, stale_and_dead.id, pid=9, silence=600.0)

    assert _reap(scope, alive_pids={7, 8}) == [stale_and_dead.id]
    assert _row(scope, stale_but_alive.id)["status"] == "running"
    assert _row(scope, dead_but_fresh.id)["status"] == "running"
    assert _row(scope, stale_and_dead.id)["status"] == "interrupted"


def test_the_reaper_leaves_the_host_that_calls_it_alone(scope, project):
    """The host reaps on every poll, so it must not reap its own row.

    Its stamp is always the oldest thing it writes next to a running step, and
    its pid is by definition alive — but the guard is the pid, not the probe.
    """
    job = _enqueue(scope, project, confirmed=True)
    _strand(scope, job.id, pid=4242, silence=600.0)
    with scope() as s:
        reaped = job_service.reap_stale(s, limit_s=60.0, keep_pid=4242,
                                        probe=lambda _p: False)
    assert reaped == []
    assert _row(scope, job.id)["status"] == "running"


def test_a_reaped_job_is_called_interrupted_not_failed(scope, project):
    """Nobody knows whether the work was half-applied, so the row says that."""
    job = _enqueue(scope, project, confirmed=True)
    _strand(scope, job.id, pid=5, silence=600.0)
    _reap(scope, alive_pids=set())
    row = _row(scope, job.id)
    assert row["status"] == "interrupted"
    assert "half-applied" in row["error"]


def test_finish_refuses_a_state_that_is_not_an_end(scope, project):
    job = _enqueue(scope, project, confirmed=True)
    with scope() as s, pytest.raises(ValueError, match="not a closing state"):
        job_service.finish(s, job.id, JobStatus.RUNNING)


def test_counters_answer_for_every_state_including_the_ones_that_are_empty(
        scope, project):
    _enqueue(scope, project, confirmed=True)
    with scope() as s:
        counts = job_service.counters(s)
    assert counts["queued"] == 1 and counts["done"] == 0
    assert set(counts) >= {status.value for status in JobStatus}


def test_open_only_hides_the_history_but_keeps_a_parked_job(
        scope, project):
    live = _enqueue(scope, project, confirmed=True)
    finished = _enqueue(scope, project, confirmed=True)
    parked = _enqueue(scope, project, confirmed=True)
    _set_status(scope, parked.id, JobStatus.WAITING_APPROVAL)
    with scope() as s:
        job_service.finish(s, finished.id, JobStatus.DONE)
    with scope() as s:
        open_ids = {j.id for j in job_service.list_jobs(s, open_only=True)}
    assert open_ids == {live.id, parked.id}


# --------------------------------------------------------------------------------------
# the host loop
# --------------------------------------------------------------------------------------


def test_an_empty_queue_leaves_the_host_idle(scope):
    host = _host(scope, [_free()])
    assert host.poll().idle is True
    assert host.poll().claimed is None


def test_the_row_is_written_before_any_work_starts(scope, project):
    """Claiming and advancing are separate polls, so a reader is never too late.

    A step that takes forty seconds must already be visible as somebody's job
    with its full queue attached before the first line of it runs.
    """
    job = _enqueue(scope, project, confirmed=True)
    host = _host(scope, [_free()])

    first = host.poll()
    assert (first.idle, first.claimed) == (False, job.id)
    row = _row(scope, job.id)
    assert row["status"] == "running" and row["pid"] == 4242
    assert [s["status"] for s in row["steps"]] == ["queued"]

    second = host.poll()
    assert (second.steps_run, second.final_status) == (1, "done")
    row = _row(scope, job.id)
    assert row["status"] == "done" and row["steps"][0]["status"] == "done"


def test_the_loop_counts_what_it_did_and_stops_when_told(scope, project):
    job = _enqueue(scope, project, confirmed=True)
    host = _host(scope, [_free(), Step(label="second", capability="",
                                       run=lambda: "ok")])
    counts = host.run(max_polls=8)
    assert counts["polls"] == 8
    assert counts["done"] == 1 and counts["steps"] == 2
    assert _row(scope, job.id)["status"] == "done"


def test_a_step_that_raises_fails_the_job_and_leaves_the_rest_queued(
        scope, project):
    """One broken step is not permission to run the steps after it.

    The queue is intact so the operator can decide whether to resume it; silently
    carrying on past a failure would report repairs that were never verified.
    """
    job = _enqueue(scope, project, confirmed=True)

    def explode() -> str:
        raise RuntimeError("boom")

    host = _host(scope, [Step(label="breaks", capability="", run=explode),
                         _free()])
    outcome = None
    for _ in range(8):
        result = host.poll()
        if result.final_status:
            outcome = result
            break
    assert outcome is not None and outcome.final_status == "failed"
    row = _row(scope, job.id)
    assert row["status"] == "failed"
    assert "boom" in row["steps"][0]["detail"]
    assert row["steps"][1]["status"] == "queued"
    assert "left untouched" in row["error"]


def test_a_job_queued_without_confirmation_is_refused_not_assumed(
        scope, project):
    """The daemon cannot satisfy an ASK because nobody is there to ask.

    This is the access matrix keeping its meaning after the human leaves the room.
    """
    _policy(scope, "access_autopilot", AccessLevel.ASK)
    job = _enqueue(scope, project, confirmed=False)
    host = _host(scope, [_free()])
    host.poll()
    row = _row(scope, job.id)
    assert row["status"] == "failed"
    assert "cannot answer for you" in row["error"]


def test_a_gate_reached_mid_queue_parks_the_job_until_someone_answers(
        scope, project):
    _policy(scope, "access_apply_to_original", AccessLevel.ASK)
    job = _enqueue(scope, project, confirmed=True)
    host = _host(scope, [_free(),
                         Step(label="edit the real files",
                              capability="apply_to_original",
                              run=lambda: "applied")])
    parked = None
    for _ in range(8):
        result = host.poll()
        if result.parked is not None:
            parked = result
            break
    assert parked is not None, "the gated step never parked"

    row = _row(scope, job.id)
    assert row["status"] == "waiting_approval"
    assert f"service approve {job.id}" in row["detail"]
    assert row["steps"][0]["status"] == "done"
    assert host.job is not None                       # still held, still resumable

    with scope() as s:
        job_service.answer(s, job.id, "skip")
    for _ in range(8):
        if host.poll().final_status:
            break
    row = _row(scope, job.id)
    assert row["status"] == "done"                    # declining is not a failure
    assert row["steps"][1]["status"] == "skipped"


def test_a_cancel_lands_between_steps_and_not_inside_one(scope, project):
    job = _enqueue(scope, project, confirmed=True)
    host = _host(scope, [_free()])
    host.poll()                                       # claims it
    with scope() as s:
        job_service.request_cancel(s, job.id)
    outcome = None
    for _ in range(8):
        result = host.poll()
        if result.final_status:
            outcome = result
            break
    assert outcome is not None and outcome.final_status == "cancelled"
    assert _row(scope, job.id)["status"] == "cancelled"


def test_a_host_that_stops_while_holding_a_job_says_so(scope, project):
    """A row left 'running' by a process that has gone is a lie readers believe."""
    from pyfix360 import service_host

    job = _enqueue(scope, project, confirmed=True)
    host = _host(scope, [_free()])
    host.poll()
    assert host.waiting is False                      # mid-step, not parked
    service_host._abandon(host)
    row = _row(scope, job.id)
    assert row["status"] == "interrupted"
    assert "half-applied" in row["error"]


def test_a_job_parked_at_a_gate_is_not_reported_as_half_applied(
        scope, project):
    """Nothing was in flight, so that wording would send the operator to a step
    that never started."""
    from pyfix360 import service_host

    _policy(scope, "access_apply_to_original", AccessLevel.ASK)
    job = _enqueue(scope, project, confirmed=True)
    host = _host(scope, [Step(label="edit the real files",
                              capability="apply_to_original",
                              run=lambda: "applied")])
    for _ in range(8):
        if host.poll().parked is not None:
            break
    assert host.waiting is True
    service_host._abandon(host)
    row = _row(scope, job.id)
    assert row["status"] == "interrupted"
    assert "waited for approval" in row["detail"]
    assert "nothing needs rolling back" in row["error"]


def test_one_host_reports_another_hosts_crash(scope, project, monkeypatch):
    """A stranded row is reaped on the next poll by whoever is awake."""
    stranded = _enqueue(scope, project, confirmed=True)
    _strand(scope, stranded.id, pid=99, silence=600.0)
    monkeypatch.setattr(job_service, "pid_alive", lambda _pid: False)

    host = _host(scope, [_free()], stale_s=60.0)
    result = host.poll()
    assert stranded.id in result.interrupted
    assert _row(scope, stranded.id)["status"] == "interrupted"
    assert host.job is None                    # the reaped row was never taken up


def test_a_finished_job_is_never_claimed_again_by_the_next_host(
        scope, project):
    job = _enqueue(scope, project, confirmed=True)
    first = _host(scope, [_free()])
    second = _host(scope, [_free()])
    for _ in range(6):
        if first.poll().final_status:
            break
    assert _row(scope, job.id)["status"] == "done"
    assert second.poll().idle is True


def test_from_settings_reads_the_persisted_interval(scope, project):
    with scope() as s:
        st = settings_service.get_settings(s)
        st.service_poll_ms = 250
        settings_service.save_settings(s, st)
    host = background_service.from_settings(session_factory=scope)
    assert host.poll_s == pytest.approx(0.25)


# --------------------------------------------------------------------------------------
# reaping helpers
# --------------------------------------------------------------------------------------


def _reap(scope, *, alive_pids: set[int], limit_s: float = 60.0) -> list[int]:
    """Reap with the liveness a test chooses, against the real clock."""
    with scope() as s:
        return job_service.reap_stale(s, limit_s=limit_s, keep_pid=-1,
                                      probe=lambda pid: pid in alive_pids)
