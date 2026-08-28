"""Constraint tests — the properties asserted by what the code must NOT do.

These are the ones that fail silently if they regress. Nothing about a core
module importing a UI library, or a stray network call, or a model dependency
appearing in the lockfile would break any behavioural test; each would simply
make a documented guarantee untrue while everything still passed.
"""

from __future__ import annotations

import ast
import socket
import tomllib
from pathlib import Path

import pytest

from comparative_judgment.core.findings import parse_findings
from comparative_judgment.core.models import Outcome
from comparative_judgment.core.session import Session
from comparative_judgment.core.store import Store

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "comparative_judgment"
CORE = SRC / "core"
UI = SRC / "ui"

#: Anything that would make this tool call a language model. The guarantee is
#: architectural, not a phase-one simplification: this tool's output is the
#: ground truth an LLM judge is measured against elsewhere, so a model inside it
#: would make the measuring instrument depend on the thing being measured.
MODEL_PACKAGES = frozenset(
    {"anthropic", "openai", "google-generativeai", "cohere", "mistralai", "litellm", "langchain"}
)

UI_PACKAGES = frozenset({"textual", "rich", "tkinter", "PyQt5", "PySide6", "flask", "fastapi"})


def _imports(path: Path) -> set[str]:
    """Every module name imported by a file, at any depth."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


class TestCoreIsUIAgnostic:
    """The seam that decides whether a web adapter is a new front end or a rewrite."""

    def test_core_imports_no_ui_library(self) -> None:
        offenders: list[str] = []
        for path in sorted(CORE.glob("*.py")):
            for imported in _imports(path):
                root = imported.split(".")[0]
                if root in UI_PACKAGES:
                    offenders.append(f"{path.name} imports {imported}")
        assert not offenders, (
            "core must not import a UI library; a browser adapter is expected later "
            f"and would inherit these: {offenders}"
        )

    def test_ui_reaches_core_only_through_the_session_interface(self) -> None:
        """A front end may use the session API and the record types. Nothing else.

        Reaching past the session into the store, the fit or the pairing logic is
        how session state ends up in a widget -- the mistake that would make this
        terminal work throwaway.
        """
        allowed = {
            "comparative_judgment.core.session",
            "comparative_judgment.core.models",
        }
        offenders: list[str] = []
        for path in sorted(UI.glob("*.py")):
            for imported in _imports(path):
                if imported.startswith("comparative_judgment.core") and imported not in allowed:
                    offenders.append(f"{path.name} imports {imported}")
        assert not offenders, f"UI must go through the session API: {offenders}"


class TestNoModelDependency:
    def test_no_language_model_package_is_declared(self) -> None:
        manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        declared = list(manifest["project"]["dependencies"])
        for group in manifest.get("dependency-groups", {}).values():
            declared.extend(group)
        names = {
            entry.split(">")[0].split("=")[0].split("[")[0].strip().lower() for entry in declared
        }
        assert not (names & MODEL_PACKAGES)

    def test_no_language_model_package_is_locked(self) -> None:
        """The manifest is the intent; the lockfile is what would install."""
        lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
        for package in MODEL_PACKAGES:
            assert f'name = "{package}"' not in lock


class TestNoNetwork:
    def test_a_full_run_opens_no_socket(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail the run if anything reaches for a socket, rather than trusting it."""

        def refuse(*args: object, **kwargs: object) -> None:
            msg = "this tool makes no network request of any kind"
            raise AssertionError(msg)

        monkeypatch.setattr(socket, "socket", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)

        document = "\n".join(
            [
                "findings:",
                *[
                    f"  - id: F-{i}\n"
                    f"    observation: obs {i}\n"
                    f"    evidence: ['line {i}: x']\n"
                    f"    consequence: cons {i}\n"
                    "    detectable_by: assert\n"
                    "    tier: defect"
                    for i in range(4)
                ],
            ]
        )
        store = Store.create(tmp_path / "s")
        loaded = parse_findings(document)
        store.put_findings(loaded.admitted)
        store.put_load_summary(loaded.excluded_questions)

        session = Session(store, rater_id="r", appearance_target=2)
        while session.next_pair() is not None:
            session.record(Outcome.LEFT)
        assert session.progress().complete


class TestRepositoryHygiene:
    def test_gitignore_covers_the_conventional_store(self) -> None:
        """The docs name .cj-store/; the ignore file must name the same path.

        If these drift the protection covers nothing, which is the failure mode
        that made this a stated rule rather than a habit.
        """
        ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert ".cj-store/" in ignored

        from comparative_judgment.cli import CONVENTIONAL_STORE

        assert f"{CONVENTIONAL_STORE}/" in ignored

    def test_gitignore_covers_credentials_and_caches(self) -> None:
        ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (".env", "__pycache__/", "*.pyc"):
            assert pattern in ignored

    def test_readme_states_the_sensitivity_and_the_store_convention(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "sensitive" in readme.lower()
        assert ".cj-store" in readme
        assert "no implicit store path" in readme.lower()

    def test_lockfile_pins_exact_versions(self) -> None:
        """Pins, not floors -- the defect this project inherited as a lesson."""
        lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
        assert 'version = "' in lock
        assert ">=" not in lock.split("[[package]]")[1].split("\n")[2]

    def test_licence_exists_and_is_apache(self) -> None:
        licence = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        assert "Apache License" in licence
        assert "Copyright 2026 Saso Gale" in licence
