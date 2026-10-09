"""Hard invariant: governance is fully deterministic — no LLM/network calls."""
from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path


def _imported_modules() -> dict[str, list[str]]:
    """Map each governance module to the module names it imports (via AST)."""
    package = importlib.import_module("verdi.governance")
    result: dict[str, list[str]] = {}
    for mod in pkgutil.walk_packages(package.__path__, prefix="verdi.governance."):
        module = importlib.import_module(mod.name)
        source_file = Path(module.__file__)
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module.split(".")[0])
        result[mod.name] = names
    return result


def test_no_llm_or_nondeterministic_imports():
    """Governance must import no LLM, network, clock, or RNG modules."""
    forbidden = {
        "httpx", "openai", "vllm", "requests", "socket", "asyncio",
        "random", "time", "datetime", "concurrent", "aiohttp",
    }
    for name, imported in _imported_modules().items():
        banned = forbidden & set(imported)
        assert not banned, f"{name} imports forbidden modules: {sorted(banned)}"


def test_engine_source_has_no_llm_markers():
    """Source must contain no model/LLM invocation markers."""
    markers = ("chat.completions", "vllm", "model.infer", "openai")
    package = importlib.import_module("verdi.governance")
    for mod in pkgutil.walk_packages(package.__path__, prefix="verdi.governance."):
        module = importlib.import_module(mod.name)
        text = Path(module.__file__).read_text(encoding="utf-8").lower()
        hits = [m for m in markers if m in text]
        assert not hits, f"{mod.name} contains LLM markers: {hits}"
