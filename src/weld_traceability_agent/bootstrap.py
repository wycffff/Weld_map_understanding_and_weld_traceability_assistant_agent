from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_source_repo(repo_root: Path | None = None) -> Path | None:
    base_dir = repo_root or Path(__file__).resolve().parents[2]
    candidates = [
        base_dir / "source_repo" / "src",
        base_dir.parent / "source_repo" / "src",
    ]
    for candidate in candidates:
        if candidate.exists():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
            return candidate

    try:
        import weld_assistant  # noqa: F401
        return None
    except ImportError:
        pass

    raise ImportError(
        "Could not import weld_assistant. Expected a sibling checkout at "
        "'source_repo/src' or an installed package."
    )
