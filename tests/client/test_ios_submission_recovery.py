from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_terminal_share_submission_has_accessible_share_extension_recovery() -> None:
    detail_source = (
        REPO_ROOT / "client/newsly/newsly/Views/SubmissionDetailView.swift"
    ).read_text()
    model_source = (
        REPO_ROOT / "client/newsly/newsly/Models/SubmissionStatusItem.swift"
    ).read_text()

    assert "if let recoveryURL = submission.recoveryURL" in detail_source
    assert "ShareLink(item: recoveryURL)" in detail_source
    assert 'Label("Share Again", systemImage: "square.and.arrow.up")' in detail_source
    assert '"submission.no_action.retry"' in detail_source
    assert '"submission.retry"' in detail_source
    assert "canRecover = isSelfSubmission && isError" in model_source
    assert 'scheme == "http" || scheme == "https"' in model_source
