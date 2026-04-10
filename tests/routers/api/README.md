# API Content Router Tests

This directory contains tests for the refactored API content router modules.

## Test Organization

The API content router has been refactored from a single large file (`app/routers/api_content.py`, previously 1604 lines) into specialized modules:

- `content_list.py` - List, search, and unread counts endpoints
- `content_detail.py` - Content detail and ChatGPT URL endpoints
- `read_status.py` - Read/unread status management
- `knowledge.py` - Knowledge-save management
- `content_actions.py` - Content transformations (convert news to article)
- `models.py` - All Pydantic request/response models

## Existing Tests

The following existing tests in `tests/routers/` continue to work without modification:

- `test_api_bulk_mark_read.py` - Tests bulk mark-read functionality
- `test_api_content_convert.py` - Tests news-to-article conversion
- `test_api_content_pagination.py` - Tests cursor-based pagination
- `test_api_content_visibility.py` - Tests content visibility rules

These tests import from `app.routers.api_content` which now serves as a backward-compatible
wrapper that imports the refactored router structure.

## New Test Structure

New tests can be added in this directory following the modular structure:

- `test_content_list.py` - Tests for listing and search endpoints
- `test_content_detail.py` - Tests for detail and chat URL endpoints
- `test_read_status.py` - Tests for read status operations
- `test_knowledge.py` - Tests for knowledge-save operations
- `test_content_actions.py` - Tests for content transformation endpoints

## Running Tests

```bash
# Activate virtual environment
. .venv/bin/activate

# Run all API tests
pytest tests/routers/ -v

# Run specific module tests
pytest tests/routers/api/test_content_list.py -v
```

## Benefits of Refactored Structure

1. **Maintainability**: Each module has a clear, single responsibility
2. **Testability**: Tests can be organized to match the module structure
3. **Navigability**: Easier to find and modify specific functionality
4. **Reusability**: Shared models in one place, easier to import
5. **Backward Compatibility**: Existing code continues to work unchanged
