"""AskBase gateway — loads the three Python agent workstreams in one process.

Agents 1–3 each ship a package literally named ``backend``; importing them
normally would shadow one another (this is why CI runs one pytest process per
agent dir). ``load_agents`` sidesteps the collision by loading each package
under a unique alias; instance attributes resolve through the alias, so the
shared "backend" name only affects initial imports.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_AGENTS = {
    "agent1": "ai-analyst",
    "agent2": "data-intelligence",
    "agent3": "sql-safety",
}

for _entry in (str(ROOT), str(ROOT / _AGENTS["agent1"])):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)


def _load_package(alias: str, directory: str):
    package_dir = ROOT / directory / "backend"
    spec = importlib.util.spec_from_file_location(
        alias,
        str(package_dir / "__init__.py"),
        submodule_search_locations=[str(package_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def load_agents():
    namespace = type("Agents", (), {})()
    for key, directory in _AGENTS.items():
        package = _load_package(f"{key}_backend", directory)
        if key == "agent1":
            sys.modules["backend"] = package  # sql_generator/agent do `from backend import ...`
            namespace.llm_client = llm_client = importlib.import_module("agent1_backend.llm_client")
            sql_generator = importlib.import_module("agent1_backend.sql_generator")
            agent = importlib.import_module("agent1_backend.agent")
            # agent.py binds `from backend import llm_client` — with the package
            # aliased that name can resolve to another agent's module, so point
            # the global at agent1's own client (patches then take effect).
            agent.llm_client = llm_client
            # and keep the exception class sql_generator catches identical
            sql_generator.LLMError = llm_client.LLMError
            namespace.generate_sql = sql_generator.generate_sql
            namespace.prompt_guard = importlib.import_module("agent1_backend.prompt_guard")
            namespace.generate_validated_sql = importlib.import_module("agent1_backend.repair_loop").generate_validated_sql
            namespace.synthesize_answer = importlib.import_module("agent1_backend.agent").synthesize_answer
        elif key == "agent2":
            sys.modules["backend"] = package
            namespace.get_schema = importlib.import_module("agent2_backend.schema_discovery").get_schema
            namespace.profile_database = importlib.import_module("agent2_backend.data_profiler").profile_database
        else:
            sys.modules["backend"] = package
            namespace.validate_sql = importlib.import_module("agent3_backend.sql_validator").validate_sql
            namespace.execute_query = importlib.import_module("agent3_backend.query_executor").execute_query
            namespace.verify_result = importlib.import_module("agent3_backend.result_verifier").verify_result
    return namespace
