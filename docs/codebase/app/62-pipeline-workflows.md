# app/pipeline/workflows/

Source folder: `app/pipeline/workflows`

## Purpose
Focused workflow helpers that model multi-step state transitions inside larger queue handlers, especially URL analysis and content processing.

## Runtime behavior
- Captures orchestration rules that would otherwise bloat task handlers, including flow protocols and transition models.
- Makes the ordering of URL-analysis and processing outcomes explicit and easier to test independently from the processor loop.

## Inventory scope
- Direct file inventory for `app/pipeline/workflows`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/pipeline/workflows/__init__.py` | n/a | Pipeline workflow orchestrators. |
| `app/pipeline/workflows/analyze_url_workflow.py` | `FeedFlowProtocol`, `TwitterFlowProtocol`, `AnalysisFlowProtocol`, `InstructionFanoutProtocol`, `PayloadCleanerProtocol`, `AnalyzeUrlWorkflow` | Workflow orchestration for ANALYZE_URL tasks. |
| `app/pipeline/workflows/content_processing_workflow.py` | `WorkflowTransition`, `ContentProcessingWorkflow` | Workflow orchestration for content processing transitions. |
