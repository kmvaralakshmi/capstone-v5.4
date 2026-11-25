"""Atomic publication of completed result directories."""
from __future__ import annotations

import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Dict, Optional

_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def publish_directory(staging: Path, target: Path) -> Optional[str]:
    """Replace a published directory from a completed same-filesystem staging directory."""
    staging, target = Path(staging), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    key = str(target.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())

    backup = target.parent / f".{target.name}_{uuid.uuid4().hex}.backup"
    with lock:
        moved_old = False
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        try:
            os.replace(staging, target)
        except OSError as exc:
            if moved_old:
                try:
                    os.replace(backup, target)
                except OSError as rollback_exc:
                    raise RuntimeError(
                        f"publishing failed ({exc}); previous results remain at {backup} "
                        f"because rollback failed ({rollback_exc})"
                    ) from exc
            raise
        if moved_old:
            try:
                shutil.rmtree(backup)
            except OSError as exc:
                return f"new results published, but old results cleanup failed: {exc}"
    return None
