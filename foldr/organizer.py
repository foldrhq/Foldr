"""
foldr.organizer
~~~~~~~~~~~~~~~
Core file-organisation engine for FOLDR v0.2.1.

Design rules
------------
- Recursive mode always targets the *root* category folders.
  Sub-directories never get their own category tree — files are lifted
  to the root's Documents/, Code/, etc.  This prevents the
  lol/Code/Code/… nesting bug.
- Every file move is recorded in an OperationRecord for undo support.
- Track directory creation ownership: only directories created by FOLDR
  during organisation are recorded in created_directories and eligible for cleanup.
- Ignore rules (from .foldrignore or --ignore patterns) are evaluated
  before any file is touched.
"""
from __future__ import annotations

import fnmatch
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from foldr.config import CATEGORIES_TEMPLATE


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class OperationRecord:
    """Single file-move record, used for undo."""
    op_id: str
    source: str          # absolute original path
    destination: str     # absolute new path
    filename: str
    category: str
    timestamp: str


@dataclass
class OrganizeResult:
    """Everything a caller / the CLI needs after a run."""
    total_items: int = 0
    skipped_directories: int = 0
    other_files: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    records: list[OperationRecord] = field(default_factory=list)
    created_directories: list[str] = field(default_factory=list)
    dry_run: bool = False
    recursive: bool = False
    dirs_processed: int = 1
    max_depth: int | None = None
    follow_symlinks: bool = False
    ignored_files: int = 0
    ignored_dirs: int = 0


# ──────────────────────────────────────────────────────────────────────────────
# Category helpers
# ──────────────────────────────────────────────────────────────────────────────

def _build_categories(base: Path, template: dict) -> dict:
    return {
        name: {
            "folder": base / data["folder"],
            "ext": set(e.lower() for e in data["ext"]),
            "count": 0,
        }
        for name, data in template.items()
    }


def _dest_folders(categories: dict) -> set[Path]:
    return {cat["folder"] for cat in categories.values()}


# ──────────────────────────────────────────────────────────────────────────────
# Ignore helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_foldrignore(base: Path) -> list[str]:
    """
    Parse .foldrignore from the root directory.
    Uses utf-8-sig to strip BOM that editors sometimes write on line 1,
    which would silently break the first pattern otherwise.
    """
    ignore_file = base / ".foldrignore"
    if not ignore_file.exists():
        return []
    patterns: list[str] = []
    for line in ignore_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _load_global_foldrignore() -> list[str]:
    """Load ~/.foldr/.foldrignore (applies when --global-ignore flag is used)."""
    p = Path.home() / ".foldr" / ".foldrignore"
    if not p.exists():
        return []
    patterns: list[str] = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _matches_any(name: str, rel_path: str, patterns: list[str]) -> bool:
    """Return True if the file/dir matches any ignore pattern."""
    for pattern in patterns:
        # directory pattern (trailing slash)
        if pattern.endswith("/"):
            if fnmatch.fnmatch(name, pattern.rstrip("/")) or fnmatch.fnmatch(rel_path, pattern.rstrip("/")):
                return True
        else:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel_path, pattern):
                return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# File-move helper
# ──────────────────────────────────────────────────────────────────────────────

def _move_file(
    destination: Path,
    source: Path,
    category: str,
    dry_run: bool,
    result: OrganizeResult,
    created_dirs_set: set[Path],
    rel_prefix: str = "",
) -> None:
    """Move source → destination, resolving name conflicts. Records the op and tracked created dirs."""
    target = destination / source.name

    if target.exists():
        counter = 1
        while True:
            candidate = destination / f"{source.stem}({counter}){source.suffix}"
            if not candidate.exists():
                target = candidate
                break
            counter += 1
        display_name = target.name
    else:
        display_name = source.name

    label = f"[{rel_prefix}] " if rel_prefix else ""
    result.actions.append(f"{label}{source.name} → {destination.name}/{display_name if display_name != source.name else ''}")

    record = OperationRecord(
        op_id=str(uuid.uuid4()),
        source=str(source),
        destination=str(target),
        filename=source.name,
        category=category,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    result.records.append(record)

    if not dry_run:
        # Track if FOLDR creates this directory during the operation
        if not destination.exists():
            destination.mkdir(parents=True, exist_ok=True)
            created_dirs_set.add(destination)
        shutil.move(str(source), str(target))


# ──────────────────────────────────────────────────────────────────────────────
# Single-directory organiser (always targets root categories)
# ──────────────────────────────────────────────────────────────────────────────

def _classify_file(
    file_path: Path,
    template: dict,
) -> tuple[str, str] | tuple[None, None]:
    """
    Return (category_name, dest_folder_name) for a single file, or (None, None).
    Used by watch mode to scope moves to one file at a time.
    """
    ext = file_path.suffix.lower()
    for cat_name, cat in template.items():
        if ext in cat.get("ext", set()):
            return cat_name, cat["folder"]
    return None, None


def _organize_dir(
    directory: Path,
    root_categories: dict,
    root_dest_folders: set[Path],
    dry_run: bool,
    ignore_patterns: list[str],
    root_base: Path,
    result: OrganizeResult,
    created_dirs_set: set[Path],
) -> None:
    """
    Organise files directly inside `directory`.
    All moves go to `root_categories` folders (rooted at root_base).
    Never creates category sub-trees inside subdirectories.
    """
    try:
        entries = list(directory.iterdir())
    except PermissionError:
        return

    result.total_items += len(entries)

    for entry in entries:
        # compute relative path for ignore matching
        try:
            rel = str(entry.relative_to(root_base))
        except ValueError:
            rel = entry.name

        if entry.is_dir():
            # Skip FOLDR's own output folders
            if entry in root_dest_folders:
                continue
            # Check ignore patterns for directories
            if _matches_any(entry.name, rel, ignore_patterns):
                result.ignored_dirs += 1
                continue
            result.skipped_directories += 1
            continue

        # Ignore check for files
        if _matches_any(entry.name, rel, ignore_patterns):
            result.ignored_files += 1
            continue

        ext = entry.suffix.lower()
        moved = False

        for cat_name, cat in root_categories.items():
            if ext in cat["ext"]:
                cat["count"] += 1
                rel_prefix = str(directory.relative_to(root_base)) if directory != root_base else ""
                _move_file(
                    destination=cat["folder"],
                    source=entry,
                    category=cat_name,
                    dry_run=dry_run,
                    result=result,
                    created_dirs_set=created_dirs_set,
                    rel_prefix=rel_prefix,
                )
                moved = True
                break

        if not moved:
            result.other_files += 1


# ──────────────────────────────────────────────────────────────────────────────
# Recursive directory collector
# ──────────────────────────────────────────────────────────────────────────────

def _collect_subdirs(
    base: Path,
    max_depth: int | None,
    follow_symlinks: bool,
    root_dest_folders: set[Path],
    ignore_patterns: list[str],
    root_base: Path,
    _depth: int = 1,
    _seen: set[int] | None = None,
) -> list[Path]:
    if _seen is None:
        _seen = set()

    result: list[Path] = []

    try:
        children = list(base.iterdir())
    except PermissionError:
        return result

    for child in children:
        if not child.is_dir():
            continue

        # Never descend into FOLDR output folders
        if child in root_dest_folders:
            continue

        # Ignore rules
        try:
            rel = str(child.relative_to(root_base))
        except ValueError:
            rel = child.name
        if _matches_any(child.name, rel, ignore_patterns):
            continue

        # Symlink safety
        if child.is_symlink():
            if not follow_symlinks:
                continue
            try:
                real_inode = child.resolve().stat().st_ino
            except OSError:
                continue
            if real_inode in _seen:
                continue
            _seen.add(real_inode)

        try:
            child.stat()
        except PermissionError:
            continue

        result.append(child)

        if max_depth is None or _depth < max_depth:
            result.extend(
                _collect_subdirs(
                    child, max_depth, follow_symlinks,
                    root_dest_folders, ignore_patterns, root_base,
                    _depth + 1, _seen,
                )
            )

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def organize_folder(
    base: Path,
    dry_run: bool = False,
    recursive: bool = False,
    max_depth: int | None = None,
    follow_symlinks: bool = False,
    extra_ignore: list[str] | None = None,
    category_template: dict | None = None,
    global_ignore: bool = False,
) -> OrganizeResult:
    """
    Organise files in `base`.

    Parameters
    ----------
    base              : root directory to organise
    dry_run           : preview only, no files moved
    recursive         : descend into subdirectories
    max_depth         : max recursion depth (None = unlimited)
    follow_symlinks   : follow symlinked directories
    extra_ignore      : additional ignore patterns (from --ignore CLI flag)
    category_template : custom category dict (from --config / user config);
                        falls back to CATEGORIES_TEMPLATE
    """
    if not base.exists() or not base.is_dir():
        raise NotADirectoryError("Path must be an existing directory.")

    template = category_template if category_template is not None else CATEGORIES_TEMPLATE

    # Build root-level categories ONCE. All files (including those found
    # recursively) are moved into these folders at the root level.
    root_categories = _build_categories(base, template)
    root_dest_folders = _dest_folders(root_categories)

    # Track folders specifically created by FOLDR during this operation
    created_dirs_set: set[Path] = set()

    # Ignore patterns: local .foldrignore + global + CLI overrides
    ignore_patterns = _load_foldrignore(base)
    if extra_ignore:
        ignore_patterns.extend(extra_ignore)
    if global_ignore:
        ignore_patterns.extend(_load_global_foldrignore())

    result = OrganizeResult(
        dry_run=dry_run,
        recursive=recursive,
        max_depth=max_depth,
        follow_symlinks=follow_symlinks,
        categories={name: 0 for name in root_categories},
    )

    # ── Organise root ──
    _organize_dir(
        directory=base,
        root_categories=root_categories,
        root_dest_folders=root_dest_folders,
        dry_run=dry_run,
        ignore_patterns=ignore_patterns,
        root_base=base,
        result=result,
        created_dirs_set=created_dirs_set,
    )
    result.dirs_processed = 1

    # ── Recursive walk ──
    if recursive:
        subdirs = _collect_subdirs(
            base=base,
            max_depth=max_depth,
            follow_symlinks=follow_symlinks,
            root_dest_folders=root_dest_folders,
            ignore_patterns=ignore_patterns,
            root_base=base,
        )
        for subdir in subdirs:
            _organize_dir(
                directory=subdir,
                root_categories=root_categories,
                root_dest_folders=root_dest_folders,
                dry_run=dry_run,
                ignore_patterns=ignore_patterns,
                root_base=base,
                result=result,
                created_dirs_set=created_dirs_set,
            )
            result.dirs_processed += 1

    # Store created directory strings for history log
    result.created_directories = [str(d) for d in created_dirs_set]

    # Update category counts from root_categories (mutated during moves)
    result.categories = {name: cat["count"] for name, cat in root_categories.items()}

    # Safe cleanup: ONLY remove empty destination folders THAT WERE CREATED BY FOLDR
    if not dry_run:
        for folder in created_dirs_set:
            if folder.exists() and folder.is_dir():
                try:
                    if not any(folder.iterdir()):
                        folder.rmdir()
                except OSError:
                    pass

    return result