"""N8 — promote partial unique index on users.clerk_user_id to a full UNIQUE constraint.

Background
----------
``services/identity/alembic/versions/f1e2d3c4b5a6_sprint1_clerk_signup_shadow.py``
added ``ix_users_clerk_user_id`` as a *partial* unique index
(``UNIQUE WHERE clerk_user_id IS NOT NULL``). That correctly prevents two
Clerk users from colliding on the same Clerk subject id while still
letting legacy rows keep a NULL value.

What it did NOT do: present a constraint that ``INSERT ... ON CONFLICT
(clerk_user_id) DO UPDATE`` can infer in *all* SQLAlchemy-managed
schemas. ``Base.metadata.create_all`` (used in the test fixture) emits a
plain column-level UNIQUE constraint from ``unique=True`` on the
``mapped_column``; production has only the partial index. The two
schemas therefore diverge on how ON CONFLICT inference resolves, and the
provision handler's SELECT-then-INSERT pattern races under concurrent
``POST /auth/clerk/provision`` calls (finding N8 in the hardening
audit): both requests pass the ``SELECT WHERE clerk_user_id = X``
guard, both attempt INSERT, the second one fails with IntegrityError
and the caller sees a 500.

Fix
---
Add a real, named ``UNIQUE`` constraint ``uq_users_clerk_user_id`` and
drop the redundant partial index. The new constraint is a full UNIQUE
(``NULLS DISTINCT`` per Postgres default), which is functionally
identical for our use case — multiple NULL values are still allowed.

With the constraint in place, the handler can use
``pg_insert(User).on_conflict_do_update(index_elements=["clerk_user_id"])``
and ON CONFLICT inference works against the same physical schema in
both prod and the test fixture.

Safety note for ops
-------------------
The partial index already prevented duplicate non-NULL ``clerk_user_id``
rows, so the new UNIQUE constraint cannot fail to create on a healthy
prod DB. If a manual SQL backdoor inserted a duplicate at some point,
``CREATE UNIQUE CONSTRAINT`` will reject the migration with
``duplicate key value violates unique constraint``. The remediation is
to ``SELECT clerk_user_id, COUNT(*) FROM users WHERE clerk_user_id IS
NOT NULL GROUP BY 1 HAVING COUNT(*) > 1;``, dedupe by keeping the
oldest row, then re-run the migration.

Revision ID: d1f2e3a4b5c6
Revises:    c0d1e2f3a4b5
Create Date: 2026-06-21
"""
from __future__ import annotations

from alembic import op

revision = "d1f2e3a4b5c6"
down_revision = "o1p2q3r4s5t6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the partial unique index first — the new UNIQUE constraint
    # will auto-create its own index, so keeping the old one would be
    # redundant duplicate index maintenance on every INSERT.
    op.drop_index("ix_users_clerk_user_id", table_name="users")

    # Full UNIQUE constraint. Postgres' default NULLS DISTINCT semantics
    # mean multiple legacy rows with NULL clerk_user_id remain allowed
    # — exactly what the partial index used to do, but now ON CONFLICT
    # (clerk_user_id) can infer this constraint without an index_where
    # predicate.
    op.create_unique_constraint(
        "uq_users_clerk_user_id",
        "users",
        ["clerk_user_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_users_clerk_user_id", "users", type_="unique",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ix_users_clerk_user_id
            ON users (clerk_user_id)
            WHERE clerk_user_id IS NOT NULL
        """,
    )
