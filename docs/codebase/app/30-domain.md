# app/domain/

Source folder: `app/domain`

## Purpose
Thin domain translation layer between SQLAlchemy ORM rows and the normalized `ContentData` model used by presenters and pipeline code.

## Runtime behavior
- Normalizes ORM data into a stable domain object so downstream code does not need to know SQLAlchemy column details.
- Concentrates conversion logic for list/detail views, worker processing, and metadata-driven rendering in one place.

## Inventory scope
- Direct file inventory for `app/domain`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/domain/__init__.py` | n/a | Domain models and business logic. |
| `app/domain/converters.py` | `content_to_domain`, `domain_to_content` | Converters between domain models and database models. |
