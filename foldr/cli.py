"""
foldr.cli
~~~~~~~~~
FOLDR v0.2.1 — Smart File Organizer
by Qasim Sethar · github.com/foldrhq/Foldr

Commands
--------
  foldr <path>                           organize
  foldr <path> --preview                 dry-run, nothing moves
  foldr <path> --recursive               include subdirectories
  foldr <path> --recursive --depth 2     limit depth
  foldr <path> --dedup keep-newest       remove duplicates (IRREVERSIBLE)
  foldr <path> --ignore "*.log" "tmp/"   skip patterns this run
  foldr <path> --no-ignore               disable all ignore rules
  foldr <path> --show-ignored            list skipped files
  foldr <path> --verbose                 print every file moved
  foldr <path> --quiet                   no output
  foldr <path> --config file.toml        custom categories

  foldr watch <path>                     start background auto-organizer
  foldr watch <path> --recursive         watch subdirectories too
  foldr unwatch <path>                   stop a watcher
  foldr watches                          list all active watchers

  foldr undo                             undo last operation
  foldr undo --id a1b2c3                 undo specific operation
  foldr undo --preview                   preview restore without moving
  foldr history                          list past operations
  foldr history --all                    all history (no limit)

  foldr config                           show config paths
  foldr config --edit                    open config.toml in editor
  foldr config --edit --ignore-file      open .foldrignore in editor

"""
from __future__ import annotations

import argparse
import os
import platform
import sys
import threading
import time
from pathlib import Path

from foldr.term import (
    ACCENT, BOLD, COL_BORD, COL_ERR, COL_OK, COL_WARN,
    FG_BRIGHT, FG_DIM, FG_MUTED, RESET, SPINNER,
    cat_fg, cat_icon, fmt_size, is_tty, ljust,
    op_icon, pad_to, pbar, strip, term_wh, truncate, vlen,
)
from foldr.config_loader import load_template
from foldr.dedup         import collect_files, find_duplicates, resolve_strategy
from foldr.empty_dirs    import remove_empty_dirs, scan_empty_dirs
from foldr.history       import (
    get_history_entry, get_latest_history,
    list_history, save_history, undo_operation,
)
from foldr.models        import DedupeStrategy
from foldr.organizer     import organize_folder

_IS_WIN = platform.system() == "Windows"
VERSION  = "0.2.1"

# ── Global flags ───────────────────────────────────────────────────────────────
_QUIET   = False
_NOCOLOR = False


# ── Output helpers ─────────────────────────────────────────────────────────────

def _w() -> int:
    return min(100, term_wh()[0])

def _c(code: str) -> str:
    return "" if _NOCOLOR else code

def _print(*args: object, **kw: object) -> None:
    if not _QUIET:
        kwargs = {}
        if 'sep' in kw:
            kwargs['sep'] = kw['sep']
        if 'end' in kw:
            kwargs['end'] = kw['end']
        if 'file' in kw:
            kwargs['file'] = kw['file']
        if 'flush' in kw:
            kwargs['flush'] = kw['flush']
        print(*args, **kwargs)

def _ok(msg: str)   -> None: _print(f"  {_c(COL_OK)}ok{_c(RESET)}  {msg}")
def _warn(msg: str) -> None: _print(f"  {_c(COL_WARN)}!{_c(RESET)}   {msg}")
def _err(msg: str)  -> None: print(f"  {_c(COL_ERR)}err{_c(RESET)} {msg}", file=sys.stderr)
def _dim(msg: str)  -> None: _print(f"  {_c(FG_MUTED)}{msg}{_c(RESET)}")
def _info(msg: str) -> None: _print(f"  {_c(ACCENT)}>{_c(RESET)} {msg}")

def _rule(title: str = "") -> None:
    if _QUIET: return
    w  = _w()
    m  = _c(FG_MUTED)
    r  = _c(RESET)
    b  = _c(BOLD)
    if not title:
        print(f"{m}{'─'*w}{r}")
        return
    side = max(1, (w - len(title) - 2) // 2)
    print(f"{m}{'─'*side}{r} {b}{title}{r} {m}{'─'*side}{r}")

def _banner() -> None:
    if _QUIET: return
    a  = _c(ACCENT + BOLD)
    d  = _c(FG_DIM)
    m  = _c(FG_MUTED)
    r  = _c(RESET)

    print()
    print(f"   {a}███████╗ ██████╗ ██╗     ██████╗ ██████╗{r}")
    print(f"   {a}██╔════╝██╔═══██╗██║     ██╔══██╗██╔══██╗{r}")
    print(f"   {a}█████╗  ██║   ██║██║     ██║  ██║██████╔╝{r}")
    print(f"   {d}██╔══╝  ██║   ██║██║     ██║  ██║██╔══██╗{r}")
    print(f"   {d}██║     ╚██████╔╝███████╗██████╔╝██║  ██║{r}")
    print(f"   {d}╚═╝      ╚═════╝ ╚══════╝╚═════╝ ╚═╝  ╚═╝{r}")

    print(f"\n   {m}Fast, Safe folder cleanup with Preview and Undo · v{VERSION}{r}")
    print(f"   {m}@qasimio · github.com/foldrhq/Foldr{r}\n")
    print()

def _box(body: str, title: str = "", col: str = "") -> None:
    """Rounded box. body may contain \\n."""
    if _QUIET: return
    c     = _c(col or COL_BORD)
    ac    = _c(ACCENT + BOLD)
    r     = _c(RESET)
    w     = _w()
    inner = w - 2    # width between the │ chars
    lines = body.split("\n")

    if title:
        t  = f" {title} "
        tl = len(t)
        pl = max(1, (inner - 2 - tl) // 2)
        pr = max(0, inner - 2 - tl - pl)
        print(f"{c}╭{'─'*pl}{r}{ac}{t}{r}{c}{'─'*pr}╮{r}")
    else:
        print(f"{c}╭{'─'*(inner-2)}╮{r}")

    for line in lines:
        rest = max(0, inner - vlen(line) - 4)
        print(f"{c}│{r}  {line}{_c(RESET)}{' '*rest}  {c}│{r}")

    print(f"{c}╰{'─'*(inner-2)}╯{r}")

def _confirm(prompt: str, default: bool = False) -> bool:
    yn = f"[{'Y' if default else 'y'}/{'n' if default else 'N'}]"
    sys.stdout.write(
        f"\n  {_c(COL_WARN+BOLD)}?{_c(RESET)}  {_c(BOLD)}{prompt}{_c(RESET)}"
        f"  {_c(FG_MUTED)}{yn}{_c(RESET)}: "
    )
    sys.stdout.flush()
    try:
        ans = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(); return False
    return (ans in ("y", "yes")) if ans else default

def _print_summary(
    cat_counts: dict[str, int],
    moved: int, other: int, ignored: int,
    elapsed: float, dry: bool,
) -> None:
    if _QUIET or not cat_counts: return
    w     = _w()
    bar_w = max(12, w // 3)
    total = max(1, sum(cat_counts.values()))
    _rule("Summary"); print()
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
        if not cnt: continue
        col  = _c(cat_fg(cat)); ico = cat_icon(cat); rst = _c(RESET)
        pct  = cnt / total; fill = max(1, int(pct * bar_w))
        bar  = _c(cat_fg(cat)) + "#"*fill + _c(FG_MUTED) + "-"*(bar_w-fill) + _c(RESET)
        print(f"  {col}{ico}{rst}  {_c(FG_DIM)}{ljust(cat,16)}{rst}  {bar}  {_c(ACCENT)}{cnt:>4}{rst}  {_c(FG_MUTED)}{pct*100:4.1f}%{rst}")
    print()
    status = (f"{_c(COL_WARN+BOLD)}preview — nothing moved{_c(RESET)}"
              if dry else f"{_c(COL_OK+BOLD)}done{_c(RESET)}")
    _box(
        f"  {status}\n\n"
        f"  {_c(ACCENT+BOLD)}{moved}{_c(RESET)} moved  "
        f"{_c(FG_MUTED)}|{_c(RESET)}  "
        f"{_c(FG_DIM)}{other}{_c(RESET)} unrecognised  "
        f"{_c(FG_MUTED)}|{_c(RESET)}  "
        f"{_c(FG_DIM)}{ignored}{_c(RESET)} ignored  "
        f"{_c(FG_MUTED)}|{_c(RESET)}  "
        f"{_c(FG_MUTED)}{elapsed:.2f}s{_c(RESET)}",
        col=COL_WARN if dry else COL_OK,
    )
    print()

def _print_preview(result: object, dry: bool) -> None:
    records = getattr(result, "records", [])
    n = len(records)
    if not n: return
    print()
    try:
        from tabulate import tabulate  # type: ignore[import]
        rows = [[truncate(r.filename, 42), Path(r.destination).parent.name+"/", r.category]
                for r in records[:80]]
        print(tabulate(rows, headers=["File","Destination","Category"],
                       tablefmt="rounded_outline"))
    except ImportError:
        for r in records[:50]:
            dest = Path(r.destination).parent.name
            col  = _c(cat_fg(r.category)); rst = _c(RESET)
            print(f"  {col}{cat_icon(r.category)}{rst}  "
                  f"{_c(FG_BRIGHT)}{truncate(r.filename,40):<40}{rst}  "
                  f"{_c(FG_MUTED)}->{rst}  {col}{dest}/{rst}")
    if n > 80: _dim(f"... and {n-80} more")
    print()
    if dry:
        _warn(f"preview — {_c(ACCENT+BOLD)}{n}{_c(RESET)} files would move. Nothing changed.")

def _load_tmpl(config_arg: str | None) -> dict | None:
    if config_arg:
        try:
            tmpl, label = load_template(Path(config_arg))
            if not _QUIET: _dim(f"config: {label}")
            return tmpl
        except FileNotFoundError as e:
            _err(str(e)); sys.exit(1)
    tmpl, label = load_template(None)
    if label and not _QUIET: _dim(f"config: {label}")
    return tmpl

def _build_ignore(args: argparse.Namespace) -> list[str]:
    if getattr(args, "no_ignore", False):
        return []
    return list(args.ignore or [])


# ── Parser ─────────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="foldr",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "WARNINGS\n"
            "  • Paths with spaces must be quoted:  foldr \"My Downloads\"\n"
            "  • --dedup permanently deletes files and cannot be undone.\n"
            "    Always run with --preview first.\n"
            "  • Watch mode runs silently — no per-file confirmation.\n"
            "\n"
            "EXAMPLES\n"
            '  foldr "D:\\Downloads"                  organize files\n'
            '  foldr "D:\\Downloads" --preview        preview changes\n'
            '  foldr "D:\\Downloads" --recursive      include subdirectories\n'
            '  foldr "D:\\Downloads" --recursive --depth 2\n'
            '  foldr "D:\\Downloads" --dedup keep-newest\n'
            '  foldr "D:\\Downloads" --ignore "*.log" "tmp/"\n'
            '  foldr "D:\\Downloads" --no-ignore      disable ignore rules\n'
            '  foldr "D:\\Downloads" --show-ignored   show skipped files\n'
            "  foldr watch ~/Downloads\n"
            "  foldr watch ~/Downloads --recursive --startup\n"
            "  foldr unwatch ~/Downloads\n"
            "  foldr watches\n"
            "  foldr undo\n"
            "  foldr undo --id <ID>\n"
            "  foldr history\n"
            "  foldr config\n"
        ),
    )
    p.add_argument("path",            nargs="?")
    p.add_argument("--preview",       action="store_true",
                   help="Show what would happen without moving files")
    p.add_argument("--recursive",     action="store_true",
                   help="Organize files in subdirectories")
    p.add_argument("--depth",         type=int, metavar="N",
                   help="Max recursion depth (use with --recursive)")
    p.add_argument("--follow-links",  action="store_true",
                   help="Follow symbolic links")
    p.add_argument("--smart",         action="store_true",
                   help="Detect file type by content (needs python-magic)")
    p.add_argument("--dedup",
                   choices=["keep-newest","keep-largest","keep-oldest"],
                   metavar="STRATEGY",
                   help="⚠ Remove duplicates — IRREVERSIBLE. Strategies: keep-newest|keep-largest|keep-oldest")
    p.add_argument("--ignore",        nargs="+", metavar="PATTERN",
                   help="Skip files matching patterns this run")
    p.add_argument("--no-ignore",     action="store_true",
                   help="Disable ALL ignore rules for this run")
    p.add_argument("--show-ignored",  action="store_true",
                   help="Show which files were skipped")
    p.add_argument("--config",        metavar="FILE",
                   help="Path to custom category config (.toml)")
    p.add_argument("--verbose",       action="store_true",
                   help="Print every file moved")
    p.add_argument("--quiet",         action="store_true",
                   help="Suppress all output (for scripts)")
    p.add_argument("--id",            metavar="ID",
                   help="History operation ID (for: foldr undo --id <ID>)")
    p.add_argument("--all",           action="store_true",
                   help="Show all history entries")
    p.add_argument("--edit",          action="store_true",
                   help="(config) open config.toml in your editor")
    p.add_argument("--ignore-file",   action="store_true", dest="ignore_file",
                   help="(config --edit) open .foldrignore instead")
    return p


# ── Subcommands ────────────────────────────────────────────────────────────────

def cmd_watch(raw: list[str], args: argparse.Namespace) -> None:
    try:
        wi    = next(i for i,a in enumerate(raw) if a=="watch")
        cands = [a for a in raw[wi+1:] if not a.startswith("-")]
        ts    = cands[0] if cands else None
    except StopIteration:
        ts = None
    target = Path(ts).resolve() if ts else Path.cwd()
    if not target.is_dir():
        _err(f"Not a valid directory: {target}")
        _dim('Tip: if path has spaces, use quotes:  foldr watch "My Downloads"')
        sys.exit(1)

    from foldr.watches import add_watch, get_watches, spawn_daemon
    if str(target.resolve()) in get_watches():
        _warn(f"Already watching: {target}")
        _dim("Run 'foldr unwatch <path>' to stop it first.")
        return

    ignore    = _build_ignore(args)
    recursive = args.recursive
    config_path = args.config
    tmpl        = _load_tmpl(config_path)

    # ── Approval prompt ────────────────────────────────────────────────────────
    mode_lbl = "preview (log only, no files moved)" if args.preview else "live"
    rec_lbl  = "yes — subdirectories included" if recursive else "no — root folder only"
    print()
    _box(
        f"  Directory:  {_c(ACCENT+BOLD)}{target}{_c(RESET)}\n"
        f"  Mode:       {_c(FG_DIM)}{mode_lbl}{_c(RESET)}\n"
        f"  Recursive:  {_c(FG_DIM)}{rec_lbl}{_c(RESET)}\n"
        f"\n"
        f"  {_c(FG_MUTED)}Watch mode will:{_c(RESET)}\n"
        f"  {_c(FG_MUTED)}  1. Organize all existing files right now (initial scan).{_c(RESET)}\n"
        f"  {_c(FG_MUTED)}  2. Keep watching — any new or moved file is organized automatically.{_c(RESET)}\n"
        f"  {_c(FG_MUTED)}  3. Files moved back to root are re-organized (no one-time limit).{_c(RESET)}\n"
        f"  {_c(FG_MUTED)}  No per-file confirmation — you are approving everything now.{_c(RESET)}",
        title=" Watch Mode ", col=COL_WARN,
    )
    if not _confirm("Start watch mode?", default=True):
        _dim("Cancelled.")
        return

    pid = spawn_daemon(
        target,
        dry_run=args.preview,
        recursive=recursive,
        extra_ignore=ignore,
        config=config_path,
        no_ignore=getattr(args, "no_ignore", False),
    )
    add_watch(
        target,
        pid,
        dry_run=args.preview,
        recursive=recursive,
        config=config_path,
        no_ignore=getattr(args, "no_ignore", False),
    )

    _info(f"Watcher started for: {_c(ACCENT+BOLD)}{target}{_c(RESET)}")
    _dim(f"PID:       {pid}")
    _dim(f"Mode:      {'preview (no moves)' if args.preview else 'live'}")
    _dim(f"Recursive: {'yes' if recursive else 'no'}")
    log_file = Path.home() / ".foldr" / "watch_logs" / (target.name + ".log")
    _dim(f"Log:       {log_file}  ← check here if nothing organizes")
    _dim(f"Stop:      foldr unwatch \"{target}\"")
    _dim(f"Status:    foldr watches")


def cmd_unwatch(raw: list[str]) -> None:
    try:
        wi    = next(i for i,a in enumerate(raw) if a=="unwatch")
        cands = [a for a in raw[wi+1:] if not a.startswith("-")]
        ts    = cands[0] if cands else None
    except StopIteration:
        ts = None

    if not ts:
        from foldr.watches import get_watches
        watches = get_watches()
        if not watches:
            _warn("No active watchers."); return
        _rule("Active Watchers")
        paths = list(watches.keys())
        for i,p in enumerate(paths,1):
            pid = watches[p].get("pid","?")
            print(f"  {_c(ACCENT)}{i}{_c(RESET)}  {_c(FG_BRIGHT)}{p}{_c(RESET)}  {_c(FG_MUTED)}PID {pid}{_c(RESET)}")
        print()
        sys.stdout.write(f"  {_c(FG_MUTED)}Number to stop (Enter=cancel): {_c(RESET)}")
        sys.stdout.flush()
        try:
            ans = input().strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if ans.isdigit() and 1<=int(ans)<=len(paths):
            ts = paths[int(ans)-1]
        else:
            _dim("Cancelled."); return

    target = Path(ts).resolve()
    from foldr.watches import kill_watch
    ok, msg = kill_watch(target)
    (_ok if ok else _warn)(msg)


def cmd_watches() -> None:
    from foldr.watches import get_watches
    watches = get_watches()
    if not watches:
        _box(
            f"  {_c(FG_DIM)}No active watchers.{_c(RESET)}\n\n"
            f"  Start one:  {_c(ACCENT)}foldr watch ~/Downloads{_c(RESET)}",
        ); return

    _rule("Active Watchers"); print()
    try:
        from tabulate import tabulate  # type: ignore[import]
        rows = []
        for p,info in watches.items():
            started   = info.get("started","")[:16].replace("T"," ")
            mode      = "preview" if info.get("dry_run") else "live"
            recursive = "yes" if info.get("recursive") else "no"
            total     = info.get("total",0)
            rows.append([Path(p).name, started, mode, recursive,
                         f"{total} files", str(info.get("pid","?"))])
        print(tabulate(rows, headers=["Directory","Started","Mode","Recursive","Organized","PID"],
                       tablefmt="rounded_outline"))
    except ImportError:
        for p,info in watches.items():
            started = info.get("started","")[:16].replace("T"," ")
            mode    = "preview" if info.get("dry_run") else "live"
            total   = info.get("total",0); pid=info.get("pid","?")
            col     = _c(COL_OK) if mode=="live" else _c(COL_WARN)
            print(f"  {_c(ACCENT+BOLD)}{ljust(Path(p).name,22)}{_c(RESET)}"
                  f"  {_c(FG_DIM)}{started}{_c(RESET)}"
                  f"  {col}{mode}{_c(RESET)}"
                  f"  {_c(FG_DIM)}{total} files  PID {pid}{_c(RESET)}")
    print()
    _dim(f"Logs:  {Path.home()/'.foldr'/'watch_logs'}")
    _dim("Stop:  foldr unwatch <directory>")


def cmd_watch_daemon(raw: list[str], args: argparse.Namespace) -> None:
    """Internal: background daemon. Not for direct user invocation."""
    try:
        wi    = raw.index("_watch-daemon")
        cands = [a for a in raw[wi+1:] if not a.startswith("-")]
        ts    = cands[0] if cands else None
    except (ValueError, IndexError):
        ts = None
    if not ts:
        sys.exit(1)
    target = Path(ts).resolve()
    tmpl   = _load_tmpl(getattr(args, "config", None))
    ignore = _build_ignore(args)
    from foldr.watch import run_watch
    run_watch(
        base=target, template=tmpl,
        dry_run=getattr(args,"preview",False),
        recursive=getattr(args,"recursive",False),
        extra_ignore=ignore, daemon_mode=True,
    )


def cmd_undo(args: argparse.Namespace) -> None:
    _rule("Undo")
    target_id = getattr(args,"id",None)
    if target_id:
        log = get_history_entry(target_id)
        if not log:
            _err(f"No history entry for ID: {target_id!r}")
            _dim("Run 'foldr history' to see valid IDs."); return
    else:
        log = get_latest_history()
        if not log:
            _warn("No history found. Run 'foldr <path>' first."); return

    ts    = log.get("timestamp","")[:19].replace("T"," ")
    base  = log.get("base",""); total=log.get("total_files",0); eid=log.get("id","?")

    _box(
        f"  ID:          {_c(ACCENT+BOLD)}{eid}{_c(RESET)}\n"
        f"  Time:        {_c(FG_DIM)}{ts}{_c(RESET)}\n"
        f"  Directory:   {_c(FG_DIM)}{base}{_c(RESET)}\n"
        f"  Files:       {_c(ACCENT+BOLD)}{total}{_c(RESET)}",
        title=" Undo Preview ", col=COL_WARN,
    )

    dry = getattr(args,"preview",False)
    if not dry:
        ok = _confirm(f"Restore {total} files from operation {eid}?", default=False)
        if not ok:
            _dim("Cancelled."); return

    result = undo_operation(log, dry_run=dry)
    print(); _rule("Preview" if dry else "Restored"); print()
    for r in result.restored:
        pfx = f"  {_c(COL_WARN)}preview{_c(RESET)}" if dry else f"  {_c(COL_OK)}<-{_c(RESET)}"
        print(f"{pfx}  {_c(FG_DIM)}{r}{_c(RESET)}")
    for s in result.skipped:
        print(f"  {_c(COL_WARN)}skip{_c(RESET)}  {_c(FG_MUTED)}{s}{_c(RESET)}")
    for e in result.errors: _err(e)
    print()
    _box(
        f"  {_c(COL_OK+BOLD)}{len(result.restored)}{_c(RESET)} restored  "
        f"{_c(FG_MUTED)}|{_c(RESET)}  "
        f"{_c(COL_WARN)}{len(result.skipped)}{_c(RESET)} skipped  "
        f"{_c(FG_MUTED)}|{_c(RESET)}  "
        f"{_c(COL_ERR)}{len(result.errors)}{_c(RESET)} errors",
        col=COL_OK if not result.errors else COL_ERR,
    )


def cmd_history(args: argparse.Namespace) -> None:
    limit   = sys.maxsize if getattr(args, "all", False) else 50
    entries = list_history(limit=limit)
    if not entries:
        _warn("No history found. Run 'foldr <path>' to start."); return
    _rule("Operation History"); print()
    try:
        from tabulate import tabulate  # type: ignore[import]
        rows = [[e.get("id","?"),
                 e.get("timestamp","")[:16].replace("T"," "),
                 Path(e.get("base","?")).name,
                 str(e.get("total_files",0))]
                for e in entries]
        print(tabulate(rows, headers=["ID","Time","Directory","Files"],
                       tablefmt="rounded_outline"))
    except ImportError:
        for e in entries:
            ts = e.get("timestamp","")[:16].replace("T"," ")
            print(f"  {_c(ACCENT)}{e.get('id','?')}{_c(RESET)}"
                  f"  {_c(FG_MUTED)}{ts}{_c(RESET)}"
                  f"  {Path(e.get('base','?')).name}"
                  f"  {_c(FG_DIM)}{e.get('total_files',0)} files{_c(RESET)}")
    print(); _dim("To undo: foldr undo --id <ID>")


def cmd_config(args: argparse.Namespace) -> None:
    foldr_dir = Path.home() / ".foldr"
    cfg_path  = foldr_dir / "config.toml"

    if getattr(args,"edit",False):
        target_file = (foldr_dir/".foldrignore") if getattr(args,"ignore_file",False) else cfg_path
        if not target_file.exists():
            target_file.parent.mkdir(parents=True, exist_ok=True)
            if getattr(args,"ignore_file",False):
                target_file.write_text(
                    "# FOLDR global ignore rules\n"
                    "# One pattern per line. Applied to every foldr run.\n"
                    "# Examples:\n# *.tmp\n# *.bak\n# desktop.ini\n",
                    encoding="utf-8",
                )
        import shutil as sh
        editor = (os.environ.get("VISUAL") or os.environ.get("EDITOR")
                  or ("notepad" if _IS_WIN else "nano"))
        if sh.which(editor):
            os.execvp(editor, [editor, str(target_file)])
        else:
            _err(f"Editor not found: {editor}")
            _dim(f"Edit manually: {target_file}")
        return

    _rule("FOLDR Config"); print()
    def _show(label: str, path: Path) -> None:
        exists = f"{_c(COL_OK)}(exists){_c(RESET)}" if path.exists() else f"{_c(FG_MUTED)}(not set){_c(RESET)}"
        _print(f"  {_c(FG_DIM)}{label:<24}{_c(RESET)}  {path}  {exists}")
    _show("Config directory",   foldr_dir)
    _show("Category config",    cfg_path)
    _show("Global ignore",      foldr_dir/".foldrignore")
    _show("History",            foldr_dir/"history")
    _show("Watch logs",         foldr_dir/"watch_logs")
    _show("Active watchers",    foldr_dir/"watches.json")
    print()
    _dim("Edit config:        foldr config --edit")
    _dim("Edit global ignore: foldr config --edit --ignore-file")


def cmd_dedup(target: Path, strat_str: str, recursive: bool,
              max_depth: int|None, preview: bool, verbose: bool) -> None:
    stmap = {"keep-newest": DedupeStrategy.KEEP_NEWEST,
             "keep-oldest": DedupeStrategy.KEEP_OLDEST,
             "keep-largest":DedupeStrategy.KEEP_LARGEST}
    _rule("Duplicate Detection")
    _warn("Dedup permanently deletes files. This CANNOT be undone via 'foldr undo'.")
    print(); _info(f"Scanning {target} ...")

    files  = collect_files(target, recursive=recursive, max_depth=max_depth)
    groups = find_duplicates(files)
    if not groups:
        _ok("No duplicates found."); return

    total_rem = sum(len(g.files)-1 for g in groups)
    _warn(f"Found {len(groups)} duplicate groups — {total_rem} files can be removed")
    for g in groups: resolve_strategy(g, stmap[strat_str])

    print()
    try:
        from tabulate import tabulate  # type: ignore[import]
        rows = [[truncate(g.keep.name,34) if g.keep else "?",
                 truncate(rem.name,34),
                 fmt_size(g.keep.stat().st_size if g.keep and g.keep.exists() else 0)]
                for g in groups[:60] for rem in g.remove]
        print(tabulate(rows, headers=["Keep","Remove","Size"], tablefmt="rounded_outline"))
    except ImportError:
        for g in groups[:30]:
            for rem in g.remove:
                print(f"  {_c(COL_OK)}keep{_c(RESET)} {g.keep.name if g.keep else '?'}"
                      f"  {_c(COL_ERR)}del{_c(RESET)}  {rem.name}")
    if total_rem>60: _dim(f"... and {total_rem-60} more")
    print()

    if preview:
        _warn(f"preview — {total_rem} files would be removed. Nothing changed."); return

    ok = _confirm(f"Permanently delete {total_rem} duplicate files? (strategy: {strat_str})",
                  default=False)
    if not ok:
        _dim("Cancelled."); return

    removed: list[Path] = []
    for g in groups:
        for p in g.remove:
            try:
                p.unlink(); removed.append(p)
                if verbose: _dim(f"deleted  {p}")
            except OSError as e:
                _err(f"Could not remove {p.name}: {e}")

    # Save to history as a dedup record
    if removed:
        from foldr.organizer import OperationRecord
        import uuid
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        recs = [OperationRecord(
            op_id=uuid.uuid4().hex[:8], source=str(p),
            destination="__deleted__", filename=p.name,
            category="duplicate", timestamp=now,
        ) for p in removed]
        save_history(recs, target, dry_run=False)

    _ok(f"Removed {len(removed)} duplicate files.")
    _dim("Recorded in history — but files cannot be restored via 'foldr undo'.")


def cmd_organize(target: Path, args: argparse.Namespace, template: dict|None) -> None:
    preview = args.preview
    ignore  = _build_ignore(args)
    verbose = args.verbose

    _rule(f"Scanning  {target.name}/")

    spinner_done = threading.Event()
    if not _QUIET and not is_tty():
        def _spin() -> None:
            i = 0
            while not spinner_done.is_set():
                sys.stdout.write(f"\r  {SPINNER[i%4]}  Scanning...")
                sys.stdout.flush(); time.sleep(0.1); i+=1
            sys.stdout.write("\r"+" "*30+"\r"); sys.stdout.flush()
        threading.Thread(target=_spin, daemon=True).start()

    t0 = time.monotonic()
    prev = organize_folder(
        base=target, dry_run=True,
        recursive=args.recursive,
        max_depth=getattr(args,"depth",None),
        follow_symlinks=getattr(args,"follow_links",False),
        extra_ignore=ignore, category_template=template,
    )
    spinner_done.set()

    if not prev.actions:
        _box(f"  {_c(COL_OK+BOLD)}Nothing to organize — directory is already tidy!{_c(RESET)}",
             col=COL_OK); return

    n = len(prev.records)
    _print_preview(prev, preview)

    if preview:
        _print_summary(
            {k:v for k,v in prev.categories.items() if v},
            n, prev.other_files, getattr(prev,"ignored_files",0),
            time.monotonic()-t0, dry=True,
        ); return

    confirmed = _confirm(f"Move {n} files?", default=True)
    if not confirmed:
        _warn("Cancelled — nothing was moved."); print(); return

    _rule("Moving files"); t_exec = time.monotonic()
    result = organize_folder(
        base=target, dry_run=False,
        recursive=args.recursive,
        max_depth=getattr(args,"depth",None),
        follow_symlinks=getattr(args,"follow_links",False),
        extra_ignore=ignore, category_template=template,
    )
    elapsed = time.monotonic()-t_exec
    moved_n = len(result.records)

    if not _QUIET:
        print(f"  {pbar(1.0,30)}  {_c(ACCENT+BOLD)}{moved_n}{_c(RESET)} files moved")

    if verbose and not _QUIET:
        print()
        for r in result.records:
            dest = Path(r.destination).parent.name
            col  = _c(cat_fg(r.category)); rst=_c(RESET)
            print(f"  {col}{cat_icon(r.category)}{rst}  "
                  f"{_c(FG_BRIGHT)}{truncate(r.filename,40):<40}{rst}  "
                  f"{_c(FG_MUTED)}->{rst}  {col}{dest}/{rst}")

    log_path = save_history(result.records, target, dry_run=False)
    if log_path and verbose: _dim(f"History: {log_path.name}")

    _print_summary(
        {k:v for k,v in result.categories.items() if v},
        moved_n, result.other_files,
        getattr(result,"ignored_files",0), elapsed, dry=False,
    )

    scan = scan_empty_dirs(target)
    if scan.found and not _QUIET:
        n_empty = len(scan.found)
        _warn(f"Found {n_empty} empty {'directory' if n_empty==1 else 'directories'}.")
        if _confirm(f"Remove {n_empty} empty directories?", default=False):
            removed = remove_empty_dirs(scan.found)
            _ok(f"Removed {len(removed.removed)} empty directories.")


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    global _QUIET, _NOCOLOR

    raw = sys.argv[1:]

    # Detect subcommand by scanning ALL positionals
    # (handles: foldr ~/path undo --id X  correctly)
    _SUBCMDS = {"watch","unwatch","watches","undo","history","config","_watch-daemon"}
    sub = next((a for a in raw if not a.startswith("-") and a in _SUBCMDS), None)

    parser = _build_parser()
    args, _ = parser.parse_known_args(raw)

    _QUIET   = args.quiet
    _NOCOLOR = not is_tty() or bool(os.environ.get("NO_COLOR"))

    if not _QUIET and sub != "_watch-daemon":
        _banner()

    if sub == "config":        cmd_config(args);              return
    if sub == "watch":         cmd_watch(raw, args);           return
    if sub == "unwatch":       cmd_unwatch(raw);               return
    if sub == "watches":       cmd_watches();                  return
    if sub == "_watch-daemon": cmd_watch_daemon(raw, args);    return
    if sub == "undo":          cmd_undo(args);                 return
    if sub == "history":       cmd_history(args);              return

    # Organize
    path_candidates = [a for a in raw if not a.startswith("-") and a not in _SUBCMDS]
    raw_path = path_candidates[0] if path_candidates else None

    if not raw_path:
        cwd = Path.cwd()
        _box(
            f"  No path given.\n\n"
            f"  Target:  {_c(ACCENT+BOLD)}{cwd}{_c(RESET)}\n\n"
            f"  {_c(FG_MUTED)}Tip: foldr ~/Downloads{_c(RESET)}\n"
            f"  {_c(FG_MUTED)}     Paths with spaces: foldr \"My Downloads\"{_c(RESET)}",
        )
        if not _confirm(f"Organize current directory ({cwd.name})?", default=False):
            _dim("Cancelled."); return
        target = cwd
    else:
        target = Path(raw_path).resolve()

    if not target.exists() or not target.is_dir():
        _err(f"Not a valid directory: {target}")
        _dim('Tip: paths with spaces need quotes — e.g.  foldr "My Downloads"')
        sys.exit(1)

    template = _load_tmpl(args.config)

    if args.dedup:
        cmd_dedup(target, args.dedup, args.recursive,
                  getattr(args,"depth",None), args.preview, args.verbose)
        return

    cmd_organize(target, args, template)


if __name__ == "__main__":
    main()