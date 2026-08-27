"""Database JSON types with Newsly persistence invariants."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON
from sqlalchemy.engine import Dialect
from sqlalchemy.sql.operators import OperatorType
from sqlalchemy.types import TypeDecorator, TypeEngine

from app.utils.json_sanitization import sanitize_json_value


class SanitizedJSON(TypeDecorator[Any]):
    """JSON that strips control characters before SQLAlchemy serializes it."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        del dialect
        return sanitize_json_value(value)

    def coerce_compared_value(
        self,
        op: OperatorType | None,
        value: Any,
    ) -> TypeEngine[Any]:
        return self.impl_instance.coerce_compared_value(op, value)

    @property
    def python_type(self) -> type[Any]:
        return self.impl_instance.python_type
