"""Tests for gateway facades in app.services.gateways."""

import subprocess
import sys
from io import BytesIO
from unittest.mock import Mock

from botocore.exceptions import ClientError

from app.models.contracts import ContentType, TaskQueue, TaskType
from app.services.gateways.http_gateway import HttpGateway, get_http_gateway
from app.services.gateways.llm_gateway import LlmGateway, get_llm_gateway
from app.services.gateways.object_storage_gateway import S3CompatibleObjectStorageGateway
from app.services.gateways.task_queue_gateway import TaskQueueGateway, get_task_queue_gateway


def _s3_gateway(client: Mock) -> S3CompatibleObjectStorageGateway:
    gateway = S3CompatibleObjectStorageGateway.__new__(S3CompatibleObjectStorageGateway)
    gateway._bucket = "content-bodies"
    gateway._client = client
    return gateway


def test_gateway_package_import_does_not_load_concrete_gateways() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.services.gateways; "
                "assert 'app.services.gateways.http_gateway' not in sys.modules; "
                "assert 'app.services.gateways.llm_gateway' not in sys.modules; "
                "assert 'app.services.gateways.task_queue_gateway' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_http_gateway_forwards_calls_and_close():
    """HttpGateway should proxy calls to underlying service/client dependencies."""
    http_service = Mock()
    robust_client = Mock()

    fetch_response = Mock()
    head_response = Mock()
    robust_response = Mock()

    http_service.fetch_content.return_value = ("body", {"content-type": "text/html"})
    http_service.fetch.return_value = fetch_response
    http_service.head.return_value = head_response
    robust_client.get.return_value = robust_response
    robust_client.head.return_value = robust_response

    gateway = HttpGateway(http_service=http_service, robust_client=robust_client)

    assert gateway.fetch_content("https://example.com") == (
        "body",
        {"content-type": "text/html"},
    )
    assert gateway.fetch("https://example.com") is fetch_response
    assert gateway.head("https://example.com", allow_statuses={404}) is head_response
    assert gateway.robust_get("https://example.com", follow_redirects=False) is robust_response
    assert gateway.robust_head("https://example.com") is robust_response

    http_service.fetch_content.assert_called_once_with("https://example.com", headers=None)
    http_service.fetch.assert_called_once_with("https://example.com", headers=None)
    http_service.head.assert_called_once_with(
        "https://example.com",
        headers=None,
        allow_statuses={404},
    )
    robust_client.get.assert_called_once_with(
        "https://example.com",
        headers=None,
        timeout=None,
        follow_redirects=False,
    )
    robust_client.head.assert_called_once_with("https://example.com", headers=None, timeout=None)

    gateway.close()
    robust_client.close.assert_called_once_with()


def test_get_http_gateway_returns_cached_instance(monkeypatch):
    """Global gateway accessor should lazily build once and then reuse."""
    from app.services.gateways import http_gateway as module

    module._http_gateway = None
    created = []

    def _build_gateway():
        gateway = Mock(spec=HttpGateway)
        created.append(gateway)
        return gateway

    monkeypatch.setattr(module, "HttpGateway", _build_gateway)

    first = get_http_gateway()
    second = get_http_gateway()

    assert first is second
    assert len(created) == 1


def test_llm_gateway_proxies_analyze_and_summarize(monkeypatch):
    """LlmGateway should delegate URL analysis and summarization to dependencies."""
    analyzer = Mock()
    analyzer.analyze_url.return_value = {"content_type": "article"}

    summarizer = Mock()
    summarizer.summarize.return_value = {"summary": "ok"}

    from app.services.gateways import llm_gateway as module

    monkeypatch.setattr(module, "get_content_analyzer", lambda: analyzer)

    gateway = LlmGateway(summarizer=summarizer)

    analysis = gateway.analyze_url("https://example.com", instruction="focus")
    summary = gateway.summarize(
        content="hello",
        content_type=ContentType.ARTICLE,
        title="T",
        max_bullet_points=4,
        max_quotes=2,
        content_id=5,
        provider_override="openai",
        model_hint="gpt-5.6-luna",
    )

    assert analysis == {"content_type": "article"}
    assert summary == {"summary": "ok"}
    analyzer.analyze_url.assert_called_once_with("https://example.com", instruction="focus")
    summarizer.summarize.assert_called_once_with(
        content="hello",
        content_type=ContentType.ARTICLE,
        title="T",
        max_bullet_points=4,
        max_quotes=2,
        content_id=5,
        provider_override="openai",
        model_hint="gpt-5.6-luna",
    )


def test_get_llm_gateway_returns_cached_instance(monkeypatch):
    """Global LLM gateway accessor should lazily build once and then reuse."""
    from app.services.gateways import llm_gateway as module

    module._llm_gateway = None
    created = []

    def _build_gateway():
        gateway = Mock(spec=LlmGateway)
        created.append(gateway)
        return gateway

    monkeypatch.setattr(module, "LlmGateway", _build_gateway)

    first = get_llm_gateway()
    second = get_llm_gateway()

    assert first is second
    assert len(created) == 1


def test_task_queue_gateway_enqueue_builds_kwargs():
    """enqueue should include only explicitly provided optional arguments."""
    queue_service = Mock()
    queue_service.enqueue.return_value = 42
    gateway = TaskQueueGateway(queue_service=queue_service)

    task_id = gateway.enqueue(TaskType.SUMMARIZE)
    assert task_id == 42
    queue_service.enqueue.assert_called_once_with(task_type=TaskType.SUMMARIZE)

    queue_service.enqueue.reset_mock()
    gateway.enqueue(
        TaskType.PROCESS_CONTENT,
        content_id=1,
        payload={"foo": "bar"},
        queue_name=TaskQueue.CONTENT,
        dedupe=True,
        dedupe_key="content|process_content|content:1",
    )
    queue_service.enqueue.assert_called_once_with(
        task_type=TaskType.PROCESS_CONTENT,
        content_id=1,
        payload={"foo": "bar"},
        queue_name=TaskQueue.CONTENT,
        dedupe=True,
        dedupe_key="content|process_content|content:1",
    )


def test_task_queue_gateway_forwards_queue_stats():
    """Queue stats should pass through transparently."""
    queue_service = Mock()
    queue_service.get_queue_stats.return_value = {"pending": 1}

    gateway = TaskQueueGateway(queue_service=queue_service)
    stats = gateway.get_queue_stats()

    queue_service.get_queue_stats.assert_called_once_with()
    assert stats == {"pending": 1}


def test_get_task_queue_gateway_returns_cached_instance(monkeypatch):
    """Global queue gateway accessor should lazily build once and then reuse."""
    from app.services.gateways import task_queue_gateway as module

    original_gateway = module._task_queue_gateway
    module._task_queue_gateway = None
    created = []

    def _build_gateway():
        gateway = Mock(spec=TaskQueueGateway)
        created.append(gateway)
        return gateway

    monkeypatch.setattr(module, "TaskQueueGateway", _build_gateway)

    try:
        first = get_task_queue_gateway()
        second = get_task_queue_gateway()

        assert first is second
        assert len(created) == 1
    finally:
        module._task_queue_gateway = original_gateway


def test_s3_put_returns_payload_metadata_without_head(monkeypatch):
    """S3 writes should not pay for a follow-up HEAD request."""
    from app.services.gateways import object_storage_gateway as module

    usage_rows = []
    monkeypatch.setattr(
        module,
        "record_vendor_usage_out_of_band",
        lambda **kwargs: usage_rows.append(kwargs),
    )
    client = Mock()
    gateway = _s3_gateway(client)

    metadata = gateway.put_bytes(
        key="articles/1.md",
        data=b"hello",
        content_type="text/markdown",
    )

    client.put_object.assert_called_once_with(
        Bucket="content-bodies",
        Key="articles/1.md",
        Body=b"hello",
        ContentType="text/markdown",
    )
    client.head_object.assert_not_called()
    assert metadata.provider == "s3_compatible"
    assert metadata.bucket == "content-bodies"
    assert metadata.key == "articles/1.md"
    assert metadata.size_bytes == 5
    assert [row["operation"] for row in usage_rows] == ["object_storage.put"]
    assert usage_rows[0]["metadata"]["size_bytes"] == 5


def test_s3_reads_and_probes_do_not_record_usage(monkeypatch):
    """Read-heavy storage calls should avoid out-of-band database writes."""
    from app.services.gateways import object_storage_gateway as module

    usage_rows = []
    monkeypatch.setattr(
        module,
        "record_vendor_usage_out_of_band",
        lambda **kwargs: usage_rows.append(kwargs),
    )
    client = Mock()
    client.get_object.return_value = {"Body": BytesIO(b"hello")}
    client.head_object.return_value = {"ContentLength": 5}
    gateway = _s3_gateway(client)

    assert gateway.get_bytes(key="articles/1.md") == b"hello"
    assert gateway.head(key="articles/1.md").size_bytes == 5
    assert gateway.exists(key="articles/1.md") is True

    assert usage_rows == []


def test_s3_exists_returns_false_for_missing_key_without_usage(monkeypatch):
    """Missing-key HEAD responses should remain a false exists result."""
    from app.services.gateways import object_storage_gateway as module

    usage_rows = []
    monkeypatch.setattr(
        module,
        "record_vendor_usage_out_of_band",
        lambda **kwargs: usage_rows.append(kwargs),
    )
    client = Mock()
    client.head_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}},
        "HeadObject",
    )
    gateway = _s3_gateway(client)

    assert gateway.exists(key="missing.md") is False
    assert usage_rows == []


def test_s3_delete_records_usage(monkeypatch):
    """Deletes remain mutation calls in vendor usage accounting."""
    from app.services.gateways import object_storage_gateway as module

    usage_rows = []
    monkeypatch.setattr(
        module,
        "record_vendor_usage_out_of_band",
        lambda **kwargs: usage_rows.append(kwargs),
    )
    client = Mock()
    gateway = _s3_gateway(client)

    gateway.delete(key="articles/1.md")

    client.delete_object.assert_called_once_with(
        Bucket="content-bodies",
        Key="articles/1.md",
    )
    assert [row["operation"] for row in usage_rows] == ["object_storage.delete"]
