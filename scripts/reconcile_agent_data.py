"""Enqueue nightly checksum reconciliation for active user corpora."""

from __future__ import annotations

from app.core.db import get_db
from app.models.db import User
from app.services.agent_data_events import (
    enqueue_agent_data_backfill,
    enqueue_agent_data_reconcile,
)
from app.services.agent_data_sync import read_agent_data_manifest


def main() -> None:
    with get_db() as db:
        user_ids = [
            int(row[0])
            for row in db.query(User.id).filter(User.is_active.is_(True)).order_by(User.id).all()
        ]
        for user_id in user_ids:
            manifest = read_agent_data_manifest(user_id)
            if manifest is None or manifest.get("complete") is not True:
                enqueue_agent_data_backfill(db, user_id=user_id)
            else:
                enqueue_agent_data_reconcile(db, user_id=user_id)
        db.commit()


if __name__ == "__main__":
    main()
