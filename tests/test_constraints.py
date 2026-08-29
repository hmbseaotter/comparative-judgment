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
from collections.abc import Iterable
from pathlib import Path

import pytest

from comparative_judgment.cli import main
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
#: Matched as substrings of a package name, not by equality, so `anthropic` also
#: catches `anthropic-bedrock` and `langchain` catches `langchain-community`. The
#: deny-list is the load-bearing artefact behind a guarantee described as
#: permanent, which is a reason to be generous with it rather than precise.
MODEL_PACKAGES = frozenset(
    {
        "anthropic",
        "openai",
        "google-generativeai",
        "google-genai",
        "vertexai",
        "cohere",
        "mistralai",
        "litellm",
        "langchain",
        "llama-index",
        "transformers",
        "huggingface-hub",
        "sentence-transformers",
        "ollama",
        "groq",
        "together",
        "replicate",
        "boto3",  # Bedrock
        "botocore",
    }
)


def _model_packages_in(names: Iterable[str]) -> list[str]:
    """Every supplied name that contains a denied package name."""
    return sorted(n for n in names if any(p in n for p in MODEL_PACKAGES))


UI_PACKAGES = frozenset({"textual", "rich", "tkinter", "PyQt5", "PySide6", "flask", "fastapi"})


def _locked_packages() -> list[dict[str, object]]:
    """Every `[[package]]` entry in the lockfile, parsed rather than grepped."""
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = lock.get("package", [])
    assert isinstance(packages, list)
    return [p for p in packages if isinstance(p, dict)]


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

    @staticmethod
    def _front_ends() -> list[Path]:
        """Every module in the package that is not core.

        Derived from the layout rather than listed. An earlier version globbed
        `ui/` alone, which made the check green while `cli.py` -- equally a front
        end, and the one actually reaching past the seam -- broke the rule it
        claimed to enforce. A check narrower than its rule is worse than no check,
        because the green is what a reader trusts.
        """
        return sorted(
            path
            for path in SRC.rglob("*.py")
            if CORE not in path.parents and path.name != "__init__.py"
        )

    def test_the_seam_scan_covers_both_front_ends(self) -> None:
        """The scan's universe must contain the CLI, not only the terminal UI."""
        covered = {path.name for path in self._front_ends()}
        assert {"cli.py", "tui.py"} <= covered, covered

    def test_front_ends_reach_core_only_through_the_session_interface(self) -> None:
        """A front end may use the session API, the record types and the errors.

        Reaching past the session into the store, the fit or the pairing logic is
        how session state ends up in a widget -- the mistake that would make this
        terminal work throwaway. `errors` is allowed because catching a named
        refusal is part of the contract, not a reach around it: the alternative is
        a front end that cannot tell a refusal from a crash.
        """
        allowed = {
            "comparative_judgment.core.session",
            "comparative_judgment.core.models",
            "comparative_judgment.core.errors",
        }
        offenders: list[str] = []
        for path in self._front_ends():
            for imported in _imports(path):
                if imported.startswith("comparative_judgment.core") and imported not in allowed:
                    offenders.append(f"{path.name} imports {imported}")
        assert not offenders, f"front ends must go through the session API: {offenders}"

    def test_no_front_end_touches_a_private_session_attribute(self) -> None:
        """`session._store` is the reach-around the import scan cannot see."""
        offenders = [
            f"{path.name}: {line.strip()}"
            for path in self._front_ends()
            for line in path.read_text(encoding="utf-8").splitlines()
            if "session._" in line or "_session._" in line
        ]
        assert not offenders, offenders


class TestNoModelDependency:
    def test_no_language_model_package_is_declared(self) -> None:
        manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        declared = list(manifest["project"]["dependencies"])
        for group in manifest.get("dependency-groups", {}).values():
            declared.extend(group)
        names = {
            entry.split(">")[0].split("=")[0].split("[")[0].strip().lower() for entry in declared
        }
        assert not _model_packages_in(names)

    def test_no_language_model_package_is_locked(self) -> None:
        """The manifest is the intent; the lockfile is what would install."""
        locked = {package["name"].lower() for package in _locked_packages()}
        assert not _model_packages_in(locked)


class TestNoNetwork:
    def test_no_module_in_the_package_imports_a_network_library(self) -> None:
        """A static scan, because it cannot be evaded by an unwalked code path.

        The runtime check below drives one route through the tool; this covers
        every module whether a test reaches it or not, which is the stronger of
        the two forms and the reason both are here.
        """
        network = {"socket", "http", "urllib", "urllib3", "requests", "httpx", "asyncio", "ssl"}
        offenders = [
            f"{path.relative_to(SRC)} imports {imported}"
            for path in sorted(SRC.rglob("*.py"))
            for imported in _imports(path)
            if imported.split(".")[0] in network
        ]
        assert not offenders, offenders

    def test_a_full_run_opens_no_socket(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail the run if anything reaches for a socket, rather than trusting it.

        Driven through `cli.main` end to end -- init, load, compare, cuts, fit,
        bands, export -- rather than through a hand-built `Session`. The earlier
        version never touched the CLI, the disk-reading findings loader or the
        severity writer, so "a full run" covered roughly half the modules.
        """

        def refuse(*args: object, **kwargs: object) -> None:
            msg = "this tool makes no network request of any kind"
            raise AssertionError(msg)

        monkeypatch.setattr(socket, "socket", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(socket, "getaddrinfo", refuse)

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
        findings_path = tmp_path / "findings.yaml"
        findings_path.write_text(document, encoding="utf-8")
        store_path = str(tmp_path / ".cj-store")

        assert main(["init", "--store", store_path]) == 0
        assert main(["load", "--store", store_path, "--findings", str(findings_path)]) == 0

        session = Session(Store.open(tmp_path / ".cj-store"), rater_id="r", appearance_target=3)
        while session.next_pair() is not None:
            session.record(Outcome.LEFT)
        assert session.progress().complete

        ranked = [e.finding_id for e in session.fit().ranked()]
        assert main(["status", "--store", store_path, "--target", "2"]) == 0
        assert main(["fit", "--store", store_path, "--target", "2"]) == 0
        assert (
            main(
                [
                    "cuts",
                    "--store",
                    store_path,
                    "--target",
                    "2",
                    "--critical-high",
                    f"{ranked[0]}:{ranked[1]}",
                    "--high-medium",
                    f"{ranked[1]}:{ranked[2]}",
                    "--medium-low",
                    f"{ranked[2]}:{ranked[3]}",
                    "--critical-high-note",
                    "calibrated against the written consequence definitions",
                ]
            )
            == 0
        )
        assert main(["bands", "--store", store_path, "--target", "2"]) == 0
        assert (
            main(
                [
                    "export",
                    "--store",
                    store_path,
                    "--target",
                    "2",
                    "--out",
                    str(tmp_path / "severity.json"),
                ]
            )
            == 0
        )


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
        """Pins, not floors -- the defect this project inherited as a lesson.

        Every package, parsed. The previous version asserted `'version = "' in
        lock` (true of any non-empty lockfile) and then inspected line index 2 of
        package block index 1 -- one line of one arbitrary package, silently
        repointed by adding or reordering a single entry. A positional index
        inside the file whose job is catching silent drift.
        """
        packages = _locked_packages()
        assert packages, "uv.lock parsed to nothing"
        unpinned = [
            f"{package['name']}: {package.get('version')!r}"
            for package in packages
            if not isinstance(package.get("version"), str)
            or any(op in str(package["version"]) for op in (">", "<", "*", "^", "~"))
        ]
        assert not unpinned, f"expressed as a floor or range rather than a pin: {unpinned}"

    def test_licence_exists_and_is_apache(self) -> None:
        licence = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        assert "Apache License" in licence
        assert "Copyright 2026 Saso Gale" in licence
