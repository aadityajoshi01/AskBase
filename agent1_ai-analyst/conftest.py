"""Put this agent's package root on sys.path so tests can `import backend...`.

Each agent keeps its own top-level module names (all of agent1's code lives under
`backend/`); importing everything from the repo root would let one agent's
`backend/` shadow another's, so tests must run per-agent (see .github/workflows/ci.yml).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
