"""Tests for gateway facades in app.services.gateways."""

from unittest.mock import Mock

from app.models.contracts import ContentType, TaskQueue, TaskType
from app.services.gateways.http_gateway import HttpGateway, get_http_gateway
from app.services.gateways.llm_gateway import LlmGateway, get_llm_gateway
from app.services.gateways.task_queue_gateway import TaskQueueGateway, get_task_queue_gateway


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
        model_hint="gpt-5.4-mini",
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
        model_hint="gpt-5.4-mini",
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


def test_task_queue_gateway_forwards_remaining_operations():
    """Other queue operations should pass through transparently."""
    queue_service = Mock()
    queue_service.dequeue.return_value = {"task_id": 9}
    queue_service.get_queue_stats.return_value = {"pending": 1}

    gateway = TaskQueueGateway(queue_service=queue_service)
    gateway.complete_task(7, success=False, error_message="boom")
    gateway.retry_task(7, delay_seconds=30)
    dequeued = gateway.dequeue(
        task_type=TaskType.SCRAPE,
        worker_id="w1",
        queue_name=TaskQueue.CONTENT,
    )
    stats = gateway.get_queue_stats()

    queue_service.complete_task.assert_called_once_with(7, success=False, error_message="boom")
    queue_service.retry_task.assert_called_once_with(7, delay_seconds=30)
    queue_service.dequeue.assert_called_once_with(
        task_type=TaskType.SCRAPE,
        worker_id="w1",
        queue_name=TaskQueue.CONTENT,
    )
    assert dequeued == {"task_id": 9}
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
