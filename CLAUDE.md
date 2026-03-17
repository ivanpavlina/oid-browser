# oidbrowser — SNMP MIB OID Browser

## What is this
Desktop app (Python + tkinter) that loads SNMP MIB files, compiles them into an OID tree via multi-pass resolution, and provides a browsable tree GUI with search and detailed MIB definitions.

## Project structure
```
main.py        — Entry point, launches tkinter GUI
models.py      — OIDNode, RawDefinition, MIBModule dataclasses
parser.py      — Regex-based single-file MIB parser (not full ASN.1)
compiler.py    — Multi-pass compiler: scan, parse, seed well-known, resolve loop, cache
gui.py         — tkinter GUI: lazy-loading tree, MIB definition panel, search, clipboard
```

## Running
```
python3 main.py
```
Load folder: `/home/exit/Nextcloud-Bulb/Documents/MIB Compilation/` (~3,457 .mib files)

## Key design decisions
- **No external dependencies** — stdlib only (tkinter, json, hashlib, re, threading)
- **Custom regex parser** — not pysmi/pyasn1. Only extracts what we display: OID hierarchy, DESCRIPTION, SYNTAX, MAX-ACCESS, STATUS. Not full ASN.1 compliant.
- **Multi-pass resolution** — loops until no more progress. ~90% resolution rate on the full MIB collection.
- **Well-known OID seeds** — ccitt(0), iso(1), internet subtree, mib-2 subtrees (system, interfaces, ip, etc.) are hardcoded to prevent name collisions from vendor MIBs.
- **Lazy tree loading** — treeview uses dummy children + expand-on-open pattern (276k nodes can't be inserted eagerly).
- **JSON cache** — compiled tree saved to `~/.cache/oidbrowser/`. Fingerprinted by file count + sum of mtimes. ~1.8s load vs ~65s compile.
- **Compilation runs in a background thread** — GUI stays responsive during load.

## Common tasks

### Adding new well-known OID roots
Edit `WELL_KNOWN` and `WELL_KNOWN_PARENTS` dicts in `compiler.py`. These nodes get `deftype="well-known"` which protects their names from being overridden during merge.

### Cache location
`~/.cache/oidbrowser/<sha256_of_folder_path>.json` — delete to force recompile.

### Parser limitations
- Only parses `OBJECT IDENTIFIER ::= { parent id }` and macro types (OBJECT-TYPE, MODULE-IDENTITY, etc.)
- Doesn't handle `{ iso 3 6 1 }` multi-component OID values (only `{ parent fragment }`)
- TEXTUAL-CONVENTION definitions are not extracted as OID nodes
- Comments inside quoted strings are handled; doubled quotes ("") are handled
