from alembic.script import ScriptDirectory


def test_revision_ids_fit_default_version_table() -> None:
    """Keep every revision compatible with Alembic's VARCHAR(32) version table."""
    revisions = ScriptDirectory("alembic").walk_revisions()
    assert all(len(revision.revision) <= 32 for revision in revisions)
