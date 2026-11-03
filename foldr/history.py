"""
foldr.history
~~~~~~~~~~~~~
Git-like history and undo for ALL foldr operations.

Design
------
Every foldr action that changes the filesystem writes ONE history entry.
Entries are immutable JSON files, newest-first by filename sort.

Git analogy
-----------
  - Each entry ≈ a git commit (a snapshot of what changed)
  - `foldr undo` ≈ `git revert HEAD` (reverse the latest op)
  - `foldr undo --id X` ≈ `git revert <sha>` (reverse a specific op)
  - Undoing op N does NOT require undoing N-1 first. Each undo is
    independent and only touches the files that specific op moved.

Why NOT "must undo in order"
-----------------------------
Requiring sequential undo (like a stack) would be frustrating:
  - User organises ~/Downloads (op A)
  - User organises ~/Documents (op B)
  - User wants to undo op A only → they shouldn't have to undo op B first

Instead we track every file individually. If a file from op A was
subsequently moved again by op B, undoing op A will skip that file
(it's no longer at the expected destination) and warn the user.
This is honest and safe: "file moved elsewhere, skipping."

Operation types
---------------
  organize     foldr ~/Downloads
  dedup        foldr ~/Downloads --dedup keep-newest
  undo         meta-record of the undo itself (so you can undo an undo)

History location
----------------
  ~/.foldr/history/YYYY-MM-DD_HH-MM-SS_<id>.json
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path


# ── Paths ─────────────────────────────────────────────────────────────────────

def history_dir() -> Path:
    return Path.home() / ".foldr" / "history"

def _ensure() -> Path:
    d = history_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Save ──────────────────────────────────────────────────────────────────────

def save_history(
    records: list,
    base: Path,
    dry_run: bool,
    op_type: str = "organize",   # "organize" | "dedup" | "undo"
    extra: dict | None = None,
) -> Path | None:
    """
    Write a history entry. Returns path or None if dry_run / no records.

    `records` is a list of OperationRecord objects (have .op_id, .source,
    .destination, .filename, .category, .timestamp attributes).
    """
    if dry_run or not records:
        return None

    d        = _ensure()
    now      = datetime.now(timezone.utc)
    eid      = uuid.uuid4().hex[:8]
    fname    = now.strftime("%Y-%m-%d_%H-%M-%S") + f"_{eid}.json"
    path     = d / fname

    payload: dict = {
        "id":          eid,
        "op_type":     op_type,
        "timestamp":   now.isoformat(),
        "base":        str(base),
        "total_files": len(records),
        "records": [
            {
                "op_id":       r.op_id,
                "source":      r.source,
                "destination": r.destination,
                "filename":    r.filename,
                "category":    getattr(r, "category", ""),
                "timestamp":   r.timestamp,
            }
            for r in records
        ],
    }
    if extra:
        payload.update(extra)

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def save_dedup_history(
    removed_paths: list[Path],
    base: Path,
    strategy: str,
) -> Path | None:
    """Save a deduplication operation to history."""
    if not removed_paths:
        return None

    d   = _ensure()
    now = datetime.now(timezone.utc)
    eid = uuid.uuid4().hex[:8]
    fname = now.strftime("%Y-%m-%d_%H-%M-%S") + f"_{eid}.json"
    path  = d / fname

    payload = {
        "id":          eid,
        "op_type":     "dedup",
        "timestamp":   now.isoformat(),
        "base":        str(base),
        "total_files": len(removed_paths),
        "strategy":    strategy,
        "records": [
            {
                "op_id":       uuid.uuid4().hex[:8],
                "source":      str(p),
                "destination": "__deleted__",
                "filename":    p.name,
                "category":    "duplicate",
                "timestamp":   now.isoformat(),
            }
            for p in removed_paths
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# ── List ──────────────────────────────────────────────────────────────────────

def list_history(limit: int = 50) -> list[dict]:
    """Return history entries, newest first."""
    d = history_dir()
    if not d.exists():
        return []
    files = sorted(d.glob("*.json"), reverse=True)[:limit]
    out = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "id":          data.get("id", f.stem[-8:]),
                "op_type":     data.get("op_type", "organize"),
                "filename":    f.name,
                "timestamp":   data.get("timestamp", ""),
                "base":        data.get("base", ""),
                "total_files": data.get("total_files", 0),
                "path":        f,
            })
        except (json.JSONDecodeError, OSError):
            continue
    return out


def get_history_entry(id_or_filename: str) -> dict | None:
    """Load a specific history entry by short ID or filename."""
    d = history_dir()
    if not d.exists():
        return None
    # Try exact ID match first, then substring match
    candidates = []
    for f in d.glob("*.json"):
        if id_or_filename == f.stem or id_or_filename in f.name:
            candidates.append(f)
    if not candidates:
        return None
    # Use most recent match
    candidates.sort(reverse=True)
    f = candidates[0]
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        data["path"] = f
        return data
    except (json.JSONDecodeError, OSError):
        return None


def get_latest_history(op_type: str | None = None) -> dict | None:
    """Return the most recent history entry, optionally filtered by op_type."""
    d = history_dir()
    if not d.exists():
        return None
    for f in sorted(d.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if op_type is None or data.get("op_type") == op_type:
                data["path"] = f
                return data
        except (json.JSONDecodeError, OSError):
            continue
    return None


# ── Undo result ───────────────────────────────────────────────────────────────

class UndoResult:
    __slots__ = ("restored", "skipped", "errors", "log_deleted", "op_type")

    def __init__(self):
        self.restored:    list[str] = []
        self.skipped:     list[str] = []
        self.errors:      list[str] = []
        self.log_deleted: bool = False
        self.op_type:     str  = "organize"

def undo_operation(log_data: dict, dry_run: bool = False) -> UndoResult:
    """
    Reverse an operation safely based on PR #31 requirements:
    1. Only clean up directories that FOLDR created ('created_directories').
    2. Only clean up directories where at least one file was SUCCESSFULLY restored.
    3. Older history entries without 'created_directories' will NOT remove directories.
    4. Process deepest-to-shallowest empty directories safely without recursive deletes.
    """
    result         = UndoResult()
    op_type        = log_data.get("op_type", "organize")
    result.op_type = op_type
    records        = log_data.get("records", [])

    if op_type == "dedup":
        for r in records:
            result.skipped.append(
                f"{r['filename']} — permanently deleted by dedup, cannot restore"
            )
        return result

    # FOLDR specifically created these folders during organize
    foldr_created_dirs = set(
        Path(d) for d in log_data.get("created_directories", [])
    )

    # Track directories that received at least one successfully restored file
    restored_dirs = set()

    for record in reversed(records):
        dest = Path(record["destination"])
        src  = Path(record["source"])

        if not dest.exists():
            result.skipped.append(
                f"{record['filename']} — not found at {dest.parent.name}/"
                f" (moved elsewhere or already restored)"
            )
            continue

        if dry_run:
            result.restored.append(
                f"{record['filename']}  {dest.parent.name}/ → {src.parent.name}/"
            )
            continue

        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            target = src

            if target.exists():
                stem = src.stem
                suf  = src.suffix
                n    = 1
                while target.exists():
                    target = src.parent / f"{stem}_restored({n}){suf}"
                    n += 1

            shutil.move(str(dest), str(target))
            result.restored.append(
                f"{record['filename']}  {dest.parent.name}/ → {target.parent.name}/"
            )
            
            # Record directory as eligible for cleanup after a successful file move
            restored_dirs.add(dest.parent)

        except OSError as e:
            result.errors.append(f"{record['filename']}: {e}")

    # Safe cleanup logic for dry_run=False
    if not dry_run and foldr_created_dirs:
        # Candidate dirs must be created by FOLDR AND have had files restored from them
        candidate_dirs = foldr_created_dirs.intersection(restored_dirs)

        # Sort deepest to shallowest
        sorted_candidates = sorted(
            candidate_dirs, key=lambda p: len(p.parts), reverse=True
        )

        for folder in sorted_candidates:
            try:
                # Remove ONLY if directory exists, is a folder, and is completely empty
                if folder.exists() and folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            except OSError:
                pass

    if not dry_run and not result.errors:
        log_path = log_data.get("path")
        if log_path:
            try:
                archive = history_dir() / "archive"
                archive.mkdir(exist_ok=True)
                log_p = Path(log_path)
                if log_p.exists():
                    log_p.rename(archive / log_p.name)
                    result.log_deleted = True
            except OSError:
                pass

    return result