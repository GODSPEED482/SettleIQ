"""Published drafts and their bindings are frozen in the database (POL-12, spec 5.7).

A published version's mappings and authority are read through its draft, so that draft must
never change again. The application role keeps UPDATE on open drafts, which is why this needs
triggers rather than a grant. The binding check reads the draft FOR SHARE so that it waits for
a publication in progress instead of passing on the old status.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

BINDING_TABLES = ("source_mappings", "authority_assertions")


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION refuse_published_draft_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status = 'PUBLISHED' THEN
                RAISE EXCEPTION 'a published ontology draft is immutable';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER ontology_drafts_published_immutable
        BEFORE UPDATE ON ontology_drafts
        FOR EACH ROW EXECUTE FUNCTION refuse_published_draft_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION refuse_binding_on_published_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            draft_status text;
        BEGIN
            SELECT status INTO draft_status FROM ontology_drafts
            WHERE tenant_id = NEW.tenant_id AND id = NEW.draft_id
            FOR SHARE;
            IF draft_status = 'PUBLISHED' THEN
                RAISE EXCEPTION 'a published ontology draft is immutable';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    for table in BINDING_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_published_immutable
            BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION refuse_binding_on_published_draft()
            """
        )


def downgrade() -> None:
    for table in BINDING_TABLES:
        op.execute(f"DROP TRIGGER {table}_published_immutable ON {table}")
    op.execute("DROP FUNCTION refuse_binding_on_published_draft()")
    op.execute("DROP TRIGGER ontology_drafts_published_immutable ON ontology_drafts")
    op.execute("DROP FUNCTION refuse_published_draft_update()")
