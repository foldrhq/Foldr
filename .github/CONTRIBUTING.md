# Contributing to FOLDR

Thank you for your interest in contributing!

## Getting Started

```bash
git clone https://github.com/foldrhq/Foldr.git
cd Foldr
python -m venv .venv
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows
pip install -e ".[dev]"
```


## Code Style

- Python 3.10+ type hints throughout
- No external dependencies in core logic (only stdlib)
- All platform-specific code inside try/except, never at module level
- Private functions prefixed with `_`; public API has no underscore

## Reporting Bugs

Open an issue at [github.com/foldrhq/Foldr/issues](https://github.com/foldrhq/Foldr/issues) with:

1. Your OS and Python version (`python --version`)
2. FOLDR version (`pip show foldr`)
3. The exact command you ran
4. The contents of `~/.foldr/watch_logs/<dir>.log` (for watch mode issues)
5. Any error output

## Pull Requests

- One fix or feature per PR
- Include a test for new functionality
- Update `CHANGELOG.md` under `[Unreleased]`
- Run `python -m pytest` before submitting

## Watch Mode Debugging

If watch mode isn't working, the log file is your first stop:

```bash
cat ~/.foldr/watch_logs/Downloads.log
```

Errors in the processor thread, initial scan failures, and import errors are all logged there.