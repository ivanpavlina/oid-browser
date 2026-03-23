# OID Browser — SNMP MIB Explorer

Desktop application for loading, compiling, and browsing SNMP MIB files. Built with Python and tkinter, zero external runtime dependencies.

**This is a fully AI-generated project.**

## Features

- Load entire folders of `.mib` files and compile them into a unified OID tree
- Multi-pass OID resolution with ~90% success rate across thousands of MIB files
- Browsable tree view with lazy loading (handles 276k+ nodes)
- Search by OID name, numeric OID, or description text
- Detailed MIB definition panel showing SYNTAX, MAX-ACCESS, STATUS, DESCRIPTION, and source module
- Full OID definition popup with complete MIB source text
- Copy OID path or name to clipboard
- Dark/light theme toggle with persistent settings
- JSON cache for fast reloads (~1.8s cached vs ~65s full compile)
- Background compilation — GUI stays responsive during load

## Screenshot

<p align="center">
  <img src="img/logo.png" alt="OID Browser" width="128">
</p>

## Running

```bash
python3 main.py
```

Requires Python 3.10+ with tkinter (stdlib). No pip dependencies.

On Fedora/RHEL, if tkinter is missing:
```bash
sudo dnf install python3-tkinter
```

## Building a standalone binary

```bash
./build.sh
```

Uses PyInstaller to produce a single executable in `dist/`. Builds for the platform you run it on (no cross-compilation).

## Project structure

```
main.py        — Entry point
models.py      — Data classes (OIDNode, RawDefinition, MIBModule)
parser.py      — Regex-based MIB file parser
compiler.py    — Multi-pass compiler with caching
gui.py         — tkinter GUI (tree, search, detail panel, themes)
build.sh       — PyInstaller build script
```

## License

MIT
