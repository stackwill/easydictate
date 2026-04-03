# Contributing

## Development Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

Add your repo-local `.env`:

```bash
GROQ_API_KEY=your_key_here
```

## Running Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Before Opening A PR

1. Run the full test suite
2. Update documentation if behavior or configuration changed
3. Keep changes focused
4. Avoid committing local secrets or machine-specific artifacts

## Scope Notes

- The daemon path is the primary supported workflow
- The GTK UI is legacy and should not be expanded unless there is a clear reason
- GNOME portal behavior should be preferred over root-only or X11-only global key hooks
