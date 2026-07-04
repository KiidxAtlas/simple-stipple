# Simple Stipple

Desktop app for drafting, tracing, and generating pattern fills for laser/vector workflows.

## Run locally

```bash
python main.py
```

## Install as app command

```bash
pipx install .
simple-stipple
```

## Optional CAD extras

```bash
pip install .[cad]
```

## Release both desktop artifacts from macOS

From your Mac, create and push a version tag to trigger the GitHub Actions release workflow for both platforms:

```bash
./scripts/release.sh v0.1.0
```

That single tag push builds and publishes:

- Windows executable: `SimpleStipple.exe`
- macOS disk image: `SimpleStipple-macOS.dmg`

If you prefer manual steps, do the equivalent:
