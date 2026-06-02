---
id: processing/pdf_extract_text
description: Prompt sent to Gemini with PDF bytes to extract readable document text for PDF and arXiv processing.
used_by: app/processing_strategies/pdf_strategy.py, app/processing_strategies/arxiv_strategy.py
prompt_type: extraction
---
Extract all text content from this PDF document.
Return the full text in a clean, readable format.
Preserve the document structure (headings, paragraphs, lists).
If you can identify the title, include it at the beginning.
