"""Language, framework and build-system detection.

Extension-based language detection plus marker-file detection for frameworks,
package managers, build systems and test harnesses. Only claims what it can
evidence from files on disk.
"""

from __future__ import annotations

from pathlib import Path

LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cxx": "cpp",
    ".cs": "csharp", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".scala": "scala", ".sh": "shell", ".ps1": "powershell",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss", ".less": "less",
    ".vue": "vue", ".svelte": "svelte",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini",
    ".cfg": "ini", ".xml": "xml", ".sql": "sql", ".md": "markdown", ".rst": "rst",
    ".dockerfile": "dockerfile",
}

# Framework markers: (relative marker file, framework name, ecosystem)
FRAMEWORK_MARKERS: list[tuple[str, str]] = [
    ("manage.py", "django"),
    ("pyproject.toml:django", "django"),
    ("requirements.txt:django", "django"),
    ("app.py:flask", "flask"),
    ("requirements.txt:flask", "flask"),
    ("pyproject.toml:fastapi", "fastapi"),
    ("requirements.txt:fastapi", "fastapi"),
    ("package.json:react", "react"),
    ("package.json:vue", "vue"),
    ("package.json:svelte", "svelte"),
    ("package.json:next", "nextjs"),
    ("package.json:express", "express"),
    ("package.json:angular", "angular"),
]

BUILD_SYSTEMS = {
    "pyproject.toml": "python (pep 621 / build backend)",
    "setup.py": "setuptools",
    "setup.cfg": "setuptools",
    "Makefile": "make",
    "CMakeLists.txt": "cmake",
    "package.json": "npm",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "Cargo.toml": "cargo",
    "go.mod": "go modules",
    "Gemfile": "bundler",
    "composer.json": "composer",
    "Dockerfile": "docker",
}

TEST_HARNESSES = {
    "pytest": ["tests", "test", "conftest.py", "pytest.ini"],
    "unittest": ["tests"],  # refined by content sniffing
    "jest": ["jest.config.js", "jest.config.ts", "jest.config.json"],
    "vitest": ["vitest.config.ts", "vitest.config.js"],
    "mocha": [".mocharc.json", ".mocharc.yml"],
}

ENTRY_POINT_HINTS = {
    "manage.py": "django management",
    "app.py": "application entry",
    "main.py": "application entry",
    "server.py": "server entry",
    "__main__.py": "module entry",
    "wsgi.py": "wsgi entry",
    "asgi.py": "asgi entry",
    "index.js": "js entry",
    "index.ts": "ts entry",
}

_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".tox", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "target", "vendor", ".idea", ".vscode", "coverage", ".next", ".nuxt",
}

_GENERATED_MARKERS = (".min.js", ".min.css", ".map", ".generated.", "_pb2.py", ".d.ts")


def is_ignored_dir(name: str) -> bool:
    return name in _IGNORE_DIRS or name.endswith(".egg-info")


def detect_language(path: Path) -> str | None:
    name = path.name.lower()
    if name == "dockerfile":
        return "dockerfile"
    return LANGUAGE_BY_EXT.get(path.suffix.lower())


def is_generated(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in _GENERATED_MARKERS)


def is_test_path(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").lower().split("/")
    name = parts[-1]
    return (
        "tests" in parts[:-1]
        or "test" in parts[:-1]
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.js")
        or name.endswith(".test.ts")
        or name.endswith(".spec.js")
        or name.endswith(".spec.ts")
    )


def _file_mentions(path: Path, needle: str, limit_bytes: int = 262_144) -> bool:
    try:
        if not path.is_file():
            return False
        data = path.read_bytes()[:limit_bytes]
        return needle.encode() in data.lower()
    except OSError:
        return False


def detect_frameworks(root: Path) -> list[str]:
    found: set[str] = set()
    for marker, framework in FRAMEWORK_MARKERS:
        if ":" in marker:
            fname, needle = marker.split(":", 1)
            fpath = root / fname
            if fname == "package.json":
                if _file_mentions(fpath, f'"{needle}"'):
                    found.add(framework)
            elif _file_mentions(fpath, needle):
                found.add(framework)
        elif (root / marker).exists():
            found.add(framework)
    return sorted(found)


def detect_build_systems(root: Path) -> list[str]:
    return [BUILD_SYSTEMS[m] for m in BUILD_SYSTEMS if (root / m).exists()]


def detect_test_harnesses(root: Path) -> list[str]:
    harnesses: list[str] = []
    if (root / "conftest.py").exists() or (root / "pytest.ini").exists():
        harnesses.append("pytest")
    tests_dir = root / "tests"
    if tests_dir.is_dir():
        has_pytest_style = any(
            p.name.startswith("test_") or p.name.endswith("_test.py")
            for p in tests_dir.rglob("*.py")
            if p.is_file()
        )
        if has_pytest_style or "pytest" in harnesses:
            if "pytest" not in harnesses:
                harnesses.append("pytest")
        else:
            harnesses.append("unittest")
    elif (root / "test").is_dir():
        harnesses.append("unittest")
    for cfg in ("jest.config.js", "jest.config.ts", "jest.config.json"):
        if (root / cfg).exists():
            harnesses.append("jest")
    return harnesses


def detect_entry_points(root: Path) -> list[str]:
    hits: list[str] = []
    for rel, label in ENTRY_POINT_HINTS.items():
        if (root / rel).exists():
            hits.append(f"{rel} ({label})")
    scripts = root / "scripts"
    if scripts.is_dir():
        hits.extend(str(p.relative_to(root)) for p in scripts.iterdir() if p.is_file())
    return hits


def detect_package_managers(root: Path) -> list[str]:
    managers = []
    if (root / "requirements.txt").exists():
        managers.append("pip (requirements.txt)")
    if (root / "pyproject.toml").exists():
        managers.append("pip/poetry/uv (pyproject.toml)")
    if (root / "Pipfile").exists():
        managers.append("pipenv")
    if (root / "package.json").exists():
        if (root / "yarn.lock").exists():
            managers.append("yarn")
        elif (root / "pnpm-lock.yaml").exists():
            managers.append("pnpm")
        else:
            managers.append("npm")
    return managers


def known_languages(root: Path) -> list[str]:
    """Languages with an extension presence in the project (shallow + deep sample)."""
    langs: set[str] = set()
    count = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if any(is_ignored_dir(p) for p in parts):
            continue
        lang = detect_language(path)
        if lang:
            langs.add(lang)
        count += 1
        if count > 20_000:  # guard for huge trees
            break
    return sorted(langs)
