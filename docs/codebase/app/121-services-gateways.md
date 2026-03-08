# app/services/gateways/

Source folder: `app/services/gateways`

## Purpose
Narrow gateway interfaces that isolate HTTP, LLM, and queue dependencies for higher-level services and workflows.

## Runtime behavior
- Wraps lower-level infrastructure behind small interfaces so workflows can depend on stable contracts instead of concrete implementations.
- Makes queue, network, and model-provider dependencies easier to stub or swap during handler/workflow execution.

## Inventory scope
- Direct file inventory for `app/services/gateways`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/services/gateways/__init__.py` | n/a | Infrastructure gateways used by pipeline and service orchestration. |
| `app/services/gateways/http_gateway.py` | `HttpGateway`, `get_http_gateway` | Unified HTTP gateway for service and workflow orchestration. |
| `app/services/gateways/llm_gateway.py` | `LlmGateway`, `get_llm_gateway` | Unified gateway for LLM analysis and summarization calls. |
| `app/services/gateways/task_queue_gateway.py` | `TaskQueueGateway`, `get_task_queue_gateway` | Queue gateway for task orchestration boundaries. |
