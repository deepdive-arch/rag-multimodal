from pathlib import Path
from zipfile import ZipFile

import pytest

from tools.package_source import PackageError, create_archive, is_forbidden_path, validate_zip_names


def test_forbidden_paths_are_detected():
    assert is_forbidden_path(".env")
    assert is_forbidden_path(".tmp/rag.db")
    assert is_forbidden_path("frontend/node_modules/react/index.js")
    assert is_forbidden_path("frontend/.next/cache/data")


def test_permitted_paths_are_accepted():
    assert not is_forbidden_path(".env.example")
    assert not is_forbidden_path("frontend/.env.local.example")
    assert not is_forbidden_path("backend/app.py")


def test_agent_skills_are_not_blocked():
    assert not is_forbidden_path(".agents/skills/nextjs-react-typescript/SKILL.md")


def test_zip_without_env_is_valid():
    validate_zip_names(["AGENTS.md", "skills-lock.json", ".env.example", ".agents/skills/SKILL.md", "api/server.py", "core/config.py", "services/storage.py", "frontend/app/page.tsx", "tests/test_api.py", "workflows/README.md"])


def test_zip_with_tmp_database_is_rejected():
    with pytest.raises(PackageError):
        validate_zip_names(["AGENTS.md", "skills-lock.json", ".env.example", ".agents/skills/SKILL.md", "api/server.py", "core/config.py", "services/storage.py", ".tmp/rag.db"])


def test_zip_with_node_modules_is_rejected():
    with pytest.raises(PackageError):
        validate_zip_names(["AGENTS.md", "skills-lock.json", ".env.example", ".agents/skills/SKILL.md", "api/server.py", "core/config.py", "services/storage.py", "frontend/node_modules/package/index.js"])


def test_archive_creates_output_directory(tmp_path: Path, monkeypatch):
    root = tmp_path / "repo"
    output = tmp_path / "nested" / "source.zip"
    root.mkdir()

    def fake_git(arguments, _root):
        archive_path = Path(next(argument.removeprefix("--output=") for argument in arguments if argument.startswith("--output=")))
        with ZipFile(archive_path, "w") as archive:
            for name in ["AGENTS.md", "skills-lock.json", ".env.example", ".agents/skills/SKILL.md", "api/server.py", "core/config.py", "services/storage.py", "frontend/app/page.tsx", "tests/test_api.py", "workflows/README.md"]:
                archive.writestr(name, "")
        return ""

    monkeypatch.setattr("tools.package_source.run_git", fake_git)
    create_archive(root, output)
    assert output.is_file()
