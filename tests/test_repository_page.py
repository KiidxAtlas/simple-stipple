from __future__ import annotations

import pytest

from simple_stipple.features.repository.page import git_failure_summary


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            "remote: Invalid username or password.\n"
            "fatal: Authentication failed for 'https://example.com/repo.git/'",
            "authentication failed",
        ),
        (
            "There is no tracking information for the current branch.\n"
            "Please specify which branch you want to merge with.",
            "no upstream branch",
        ),
        ("CONFLICT (content): Merge conflict in part.dxf", "conflicting changes"),
        (
            " ! [rejected]        main -> main (fetch first)\n"
            "error: failed to push some refs to 'origin'",
            "the remote has newer commits",
        ),
        (
            "fatal: unable to access 'https://example.com/': Could not resolve host: example.com",
            "network error",
        ),
        ("\nTimed out", "timed out"),
    ],
)
def test_failed_git_step_is_named_by_its_cause_and_key_line(output: str, expected: str) -> None:
    summary = git_failure_summary([(["fetch"], True, ""), (["push"], False, output)])

    assert summary.startswith(expected)
    assert "(" in summary  # the quoted git line follows the cause


def test_unrecognised_failure_quotes_the_fatal_line() -> None:
    output = "hint: something\nfatal: bad object HEAD.\nhint: more"

    assert git_failure_summary([(["pull"], False, output)]) == "fatal: bad object HEAD"


def test_cancelled_run_is_reported_without_git_noise() -> None:
    assert git_failure_summary([(["pull"], False, "Receiving objects\nCancelled")]) == "cancelled"
