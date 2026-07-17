"""Create and validate a safe source archive from the Git tree."""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath


FORBIDDEN_DIRECTORY_NAMES = {".tmp", ".venv", "venv", "env", "node_modules", ".next", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "htmlcov", "coverage", "dist"}
FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".zip")
FORBIDDEN_FILENAMES = {"credentials.json", "token.json"}
REQUIRED_PREFIXES = (".agents/skills/", "api/", "core/", "services/", "frontend/", "tests/", "workflows/")


class PackageError(RuntimeError):
    """Raised when the repository cannot produce a safe package."""


def project_root() -> Path:
    """Return the repository root containing this tool."""
    return Path(__file__).resolve().parents[1]


def normalize_path(path: str | Path) -> str:
    """Normalize a repository or ZIP path to POSIX form."""
    return PurePosixPath(str(path).replace("\\", "/")).as_posix().removeprefix("./")


def is_example_environment(path: str) -> bool:
    """Allow only environment examples, never live environment files."""
    name = PurePosixPath(path).name
    return name.startswith(".env") and name.endswith(".example")


def is_forbidden_path(path: str | Path) -> bool:
    """Return whether a repository or archive path is prohibited."""
    normalized = normalize_path(path)
    parts = PurePosixPath(normalized).parts
    name = parts[-1] if parts else ""
    if normalized == ".tmp/.gitkeep":
        return False
    return False if is_example_environment(normalized) else name in FORBIDDEN_FILENAMES or name == ".env" or name.startswith(".env.") or any(part in FORBIDDEN_DIRECTORY_NAMES for part in parts) or name.endswith(FORBIDDEN_SUFFIXES)


def run_git(arguments: list[str], root: Path) -> str:
    """Run a Git command without invoking a shell."""
    result = subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def tracked_paths(root: Path) -> list[str]:
    """Return tracked paths from HEAD without reading file contents."""
    return [normalize_path(path) for path in run_git(["ls-files"], root).splitlines() if path]


def validate_repository(root: Path) -> list[str]:
    """Validate repository identity, tracked paths and required content."""
    actual_root = Path(run_git(["rev-parse", "--show-toplevel"], root)).resolve()
    if actual_root != root.resolve():
        raise PackageError("A raiz do projeto não coincide com a raiz do repositório Git")
    paths = tracked_paths(root)
    forbidden = [path for path in paths if is_forbidden_path(path)]
    if forbidden:
        raise PackageError(f"Caminhos proibidos rastreados: {', '.join(forbidden)}")
    validate_required_paths(paths)
    return paths


def validate_required_paths(paths: list[str]) -> None:
    """Ensure the source tree includes the required project areas."""
    required = {"AGENTS.md", "skills-lock.json", ".env.example"}
    missing = sorted(path for path in required if path not in paths)
    missing.extend(prefix for prefix in REQUIRED_PREFIXES if not any(path.startswith(prefix) for path in paths))
    if missing:
        raise PackageError(f"Conteúdo obrigatório ausente: {', '.join(missing)}")


def validate_zip_names(names: list[str]) -> None:
    """Reject forbidden ZIP entries and confirm required source entries."""
    normalized = [normalize_path(name) for name in names]
    forbidden = [name for name in normalized if is_forbidden_path(name)]
    if forbidden:
        raise PackageError(f"Caminhos proibidos no ZIP: {', '.join(forbidden)}")
    validate_required_paths(normalized)


def create_archive(root: Path, output: Path) -> None:
    """Create a ZIP archive from HEAD and validate its entry names."""
    output.parent.mkdir(parents=True, exist_ok=True)
    run_git(["archive", "--format=zip", f"--output={output}", "HEAD"], root)
    with zipfile.ZipFile(output) as archive:
        validate_zip_names(archive.namelist())


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    """Parse the package command options."""
    parser = argparse.ArgumentParser(description="Empacota somente o código-fonte versionado")
    parser.add_argument("--check", action="store_true", help="valida o repositório sem criar ZIP")
    parser.add_argument("--output", type=Path, default=Path("dist/Agent-RAG-source.zip"))
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    """Validate the repository and optionally create its source ZIP."""
    options = parse_args(arguments)
    root = project_root()
    try:
        validate_repository(root)
        if not options.check:
            output = options.output if options.output.is_absolute() else root / options.output
            create_archive(root, output)
    except (PackageError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"Erro de empacotamento: {error}", file=sys.stderr)
        return 1
    print("Repositório validado." if options.check else "Pacote de código-fonte criado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
