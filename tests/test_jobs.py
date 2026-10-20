"""Behavior of the shared keyed background-job runner."""

from __future__ import annotations

import threading

import pytest
from PySide6.QtWidgets import QApplication

from simple_stipple.platform.jobs import JobCancelled, JobRunner, Policy


@pytest.fixture
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _drain(app: QApplication, runner: JobRunner, *keys: str) -> None:
    for _ in range(200):
        for key in keys:
            runner.wait(key, timeout=2)
        app.processEvents()
        if not any(runner.is_busy(key) for key in keys):
            return
    raise AssertionError("jobs did not finish")


def _blocking(release: threading.Event, value: object):
    def work(cancel: threading.Event) -> object:
        while not release.wait(0.01):
            if cancel.is_set():
                raise JobCancelled
        return value

    return work


def test_result_is_delivered_on_the_gui_thread(app) -> None:
    runner = JobRunner()
    seen: list[tuple[object, bool]] = []
    runner.submit(
        "k",
        lambda _cancel: 42,
        on_done=lambda value: seen.append(
            (value, threading.current_thread() is threading.main_thread())
        ),
    )
    _drain(app, runner, "k")
    assert seen == [(42, True)]


def test_rapid_requests_cancel_the_running_job_and_only_the_newest_runs(app) -> None:
    runner = JobRunner()
    release = threading.Event()
    results: list[object] = []
    first = runner.submit("k", _blocking(release, "first"), on_done=results.append)
    for index in range(100):
        runner.submit("k", lambda _c, index=index: index, on_done=results.append)
    assert first.is_set()
    assert runner.is_running("k") and runner.is_busy("k")
    _drain(app, runner, "k")
    assert results == [99]


def test_replace_starts_immediately_and_drops_the_stalled_result(app) -> None:
    runner = JobRunner()
    stalled = threading.Event()
    results: list[object] = []
    runner.submit("k", lambda cancel: stalled.wait(5) and "old", on_done=results.append)
    runner.submit("k", lambda _cancel: "new", on_done=results.append, policy=Policy.REPLACE)
    _drain(app, runner, "k")
    stalled.set()
    for _ in range(50):
        app.processEvents()
    assert results == ["new"]
    assert runner.shutdown(timeout=2)


def test_explicit_cancel_reports_cancellation_not_a_result(app) -> None:
    runner = JobRunner()
    release = threading.Event()
    events: list[str] = []
    runner.submit(
        "k",
        _blocking(release, "value"),
        on_done=lambda _v: events.append("done"),
        on_cancelled=lambda: events.append("cancelled"),
    )
    assert runner.cancel("k")
    _drain(app, runner, "k")
    assert events == ["cancelled"]


def test_errors_reach_on_error(app) -> None:
    runner = JobRunner()
    errors: list[BaseException] = []

    def fail(_cancel):
        raise ValueError("boom")

    runner.submit("k", fail, on_error=errors.append)
    _drain(app, runner, "k")
    assert [str(error) for error in errors] == ["boom"]


def test_keys_are_independent(app) -> None:
    runner = JobRunner()
    results: list[str] = []
    runner.submit("a", lambda _c: "a", on_done=results.append)
    runner.submit("b", lambda _c: "b", on_done=results.append)
    _drain(app, runner, "a", "b")
    assert sorted(results) == ["a", "b"]


def test_shutdown_cancels_joins_and_silences_callbacks(app) -> None:
    runner = JobRunner()
    release = threading.Event()
    events: list[object] = []
    runner.submit(
        "k",
        _blocking(release, "value"),
        on_done=events.append,
        on_cancelled=lambda: events.append("cancelled"),
    )
    runner.submit("k", lambda _c: "queued", on_done=events.append)
    assert runner.shutdown(timeout=2)
    for _ in range(20):
        app.processEvents()
    assert events == []
    assert runner.submit("k", lambda _c: "late", on_done=events.append).is_set()
