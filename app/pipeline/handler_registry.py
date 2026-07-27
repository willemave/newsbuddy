"""Lazy, queue-scoped task handler composition."""

from __future__ import annotations

from importlib import import_module

from app.models.contracts import TaskQueue
from app.pipeline.task_handler import TaskHandler
from app.pipeline.task_specs import TASK_SPECS


def build_handlers_for_queue(queue: TaskQueue | str) -> list[TaskHandler]:
    """Import and instantiate only handlers declared for one queue."""
    queue_name = queue.value if isinstance(queue, TaskQueue) else TaskQueue(queue).value
    handlers: list[TaskHandler] = []
    for task_type, task_spec in TASK_SPECS.items():
        if task_spec.queue.value != queue_name:
            continue
        handler_class = getattr(
            import_module(task_spec.handler_module),
            task_spec.handler_class,
        )
        handler = handler_class()
        if handler.task_type != task_type:
            raise RuntimeError(
                f"Handler {task_spec.handler_class} declares {handler.task_type}, "
                f"expected {task_type}"
            )
        handlers.append(handler)
    return handlers
