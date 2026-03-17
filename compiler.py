"""Multi-pass compiler: scans MIB files, resolves dependencies, builds OID tree."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Callable

from models import OIDNode, MIBModule, RawDefinition
from parser import parse_mib_file

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "oidbrowser")


# Well-known OID tree roots
WELL_KNOWN = {
    "ccitt":        "0",
    "iso":          "1",
    "joint-iso-ccitt": "2",
    "org":          "1.3",
    "dod":          "1.3.6",
    "internet":     "1.3.6.1",
    "directory":    "1.3.6.1.1",
    "mgmt":         "1.3.6.1.2",
    "mib-2":        "1.3.6.1.2.1",
    "transmission": "1.3.6.1.2.1.10",
    "experimental": "1.3.6.1.3",
    "private":      "1.3.6.1.4",
    "enterprises":  "1.3.6.1.4.1",
    "security":     "1.3.6.1.5",
    "snmpV2":       "1.3.6.1.6",
    "snmpDomains":  "1.3.6.1.6.1",
    "snmpProxys":   "1.3.6.1.6.2",
    "snmpModules":  "1.3.6.1.6.3",
    "zeroDotZero":  "0.0",
    # mib-2 subtrees (RFC1213 / SNMPv2-MIB canonical names)
    "system":       "1.3.6.1.2.1.1",
    "interfaces":   "1.3.6.1.2.1.2",
    "at":           "1.3.6.1.2.1.3",
    "ip":           "1.3.6.1.2.1.4",
    "icmp":         "1.3.6.1.2.1.5",
    "tcp":          "1.3.6.1.2.1.6",
    "udp":          "1.3.6.1.2.1.7",
    "egp":          "1.3.6.1.2.1.8",
    "snmp":         "1.3.6.1.2.1.11",
}

# Parent relationships for well-known nodes
WELL_KNOWN_PARENTS = {
    "iso":          ("ccitt", 1),  # special: iso is root 1
    "org":          ("iso", 3),
    "dod":          ("org", 6),
    "internet":     ("dod", 1),
    "directory":    ("internet", 1),
    "mgmt":         ("internet", 2),
    "mib-2":        ("mgmt", 1),
    "transmission": ("mib-2", 10),
    "experimental": ("internet", 3),
    "private":      ("internet", 4),
    "enterprises":  ("private", 1),
    "security":     ("internet", 5),
    "snmpV2":       ("internet", 6),
    "snmpDomains":  ("snmpV2", 1),
    "snmpProxys":   ("snmpV2", 2),
    "snmpModules":  ("snmpV2", 3),
    # mib-2 subtrees
    "system":       ("mib-2", 1),
    "interfaces":   ("mib-2", 2),
    "at":           ("mib-2", 3),
    "ip":           ("mib-2", 4),
    "icmp":         ("mib-2", 5),
    "tcp":          ("mib-2", 6),
    "udp":          ("mib-2", 7),
    "egp":          ("mib-2", 8),
    "snmp":         ("mib-2", 11),
}


class MIBCompiler:
    """Compiles MIB files into a unified OID tree."""

    def __init__(self):
        self.root = OIDNode(name="root", oid_fragment=0, full_oid="")
        self.symbol_table: dict[str, OIDNode] = {}
        self.modules: list[MIBModule] = []
        self.all_defs: list[tuple[RawDefinition, str]] = []  # (def, module_name)
        self.resolved_names: set[str] = set()
        self.total_defs = 0
        self.resolved_count = 0
        self.file_count = 0
        self.unresolved_defs: list[tuple[RawDefinition, str]] = []  # (def, module_name)
        self.source_folder: str = ""

    def compile_folder(
        self,
        folder: str,
        progress_cb: Callable[[str, int, int], None] | None = None,
        force: bool = False,
    ) -> tuple[OIDNode, int, int]:
        """Compile all MIB files in folder. Returns (root, resolved, total).

        progress_cb(phase, current, total) is called for progress updates.
        force=True skips cache and recompiles from scratch.
        """
        self.source_folder = folder

        # Try loading from cache (unless forced)
        if not force:
            if progress_cb:
                progress_cb("cache", 0, 0)
            if self._load_cache(folder):
                if progress_cb:
                    progress_cb("done", self.resolved_count, self.total_defs)
                return self.root, self.resolved_count, self.total_defs

        # Phase 1: Scan for files
        files = self._scan_files(folder)
        self.file_count = len(files)
        if progress_cb:
            progress_cb("scan", len(files), len(files))

        # Phase 2: Parse files
        self.modules = []
        self.all_defs = []
        for i, filepath in enumerate(files):
            mods = parse_mib_file(filepath)
            for mod in mods:
                self.modules.append(mod)
                for d in mod.definitions:
                    self.all_defs.append((d, mod.name))
            if progress_cb and (i % 50 == 0 or i == len(files) - 1):
                progress_cb("parse", i + 1, len(files))

        self.total_defs = len(self.all_defs)

        # Phase 3: Seed well-known roots
        self._seed_well_known()

        # Phase 4: Multi-pass resolution
        self._resolve_loop(progress_cb)

        # Phase 5: Clear all old caches, save new one, mark as last used
        self._purge_cache()
        self._save_cache(folder)
        self._save_last(folder)

        return self.root, self.resolved_count, self.total_defs

    def _scan_files(self, folder: str) -> list[str]:
        """Recursively find all MIB files."""
        files = []
        for dirpath, _dirnames, filenames in os.walk(folder):
            for fname in filenames:
                lower = fname.lower()
                if lower.endswith(('.mib', '.cmi', '.cds', '.my', '.txt')):
                    files.append(os.path.join(dirpath, fname))
                elif '.' not in fname:
                    # Extensionless files (like HARMONIC ones)
                    files.append(os.path.join(dirpath, fname))
        return files

    def _seed_well_known(self) -> None:
        """Add well-known OID nodes to the tree."""
        # First create ccitt(0), iso(1), joint-iso-ccitt(2) under root
        for name, oid_str in [("ccitt", "0"), ("iso", "1"), ("joint-iso-ccitt", "2")]:
            frag = int(oid_str.split(".")[-1])
            node = OIDNode(
                name=name, oid_fragment=frag, full_oid=oid_str,
                module="(well-known)", deftype="well-known"
            )
            self.root.add_child(node)
            self.symbol_table[name] = node

        # Now add the rest using parent relationships
        for name, (parent_name, frag) in WELL_KNOWN_PARENTS.items():
            if name in ("iso",):
                continue  # already added
            parent = self.symbol_table.get(parent_name)
            if not parent:
                continue
            oid_str = WELL_KNOWN[name]
            node = OIDNode(
                name=name, oid_fragment=frag, full_oid=oid_str,
                module="(well-known)", deftype="well-known"
            )
            parent.add_child(node)
            self.symbol_table[name] = node

    def _resolve_loop(
        self,
        progress_cb: Callable[[str, int, int], None] | None = None,
    ) -> None:
        """Multi-pass resolution until no more progress."""
        unresolved = list(range(len(self.all_defs)))
        self.resolved_count = 0
        pass_num = 0

        while True:
            pass_num += 1
            newly_resolved = []

            for idx in unresolved:
                defn, mod_name = self.all_defs[idx]

                # Skip if already resolved (e.g. duplicate name)
                if defn.name in self.resolved_names:
                    newly_resolved.append(idx)
                    continue

                parent = self.symbol_table.get(defn.parent_name)
                if parent is None:
                    continue

                # Check if this fragment already exists under parent
                existing = parent.get_child(defn.fragment)
                if existing:
                    is_well_known = existing.deftype == "well-known"
                    # Merge: update if the new def has more info
                    if defn.description and not existing.description:
                        existing.description = defn.description
                    if defn.syntax and not existing.syntax:
                        existing.syntax = defn.syntax
                    if defn.max_access and not existing.max_access:
                        existing.max_access = defn.max_access
                    if defn.status and not existing.status:
                        existing.status = defn.status
                    if not is_well_known:
                        if defn.deftype and not existing.deftype:
                            existing.deftype = defn.deftype
                        if mod_name and not existing.module:
                            existing.module = mod_name
                        # Prefer OBJECT-TYPE/OBJECT IDENTIFIER names over
                        # MODULE-IDENTITY names (which often collide with subtree names)
                        if (defn.deftype != "MODULE-IDENTITY" and
                                existing.deftype == "MODULE-IDENTITY"):
                            existing.name = defn.name
                    else:
                        # For well-known: accept module info but preserve name
                        if mod_name and existing.module == "(well-known)":
                            existing.module = mod_name
                    # Also register the name as a symbol pointing to this node
                    if defn.name not in self.symbol_table:
                        self.symbol_table[defn.name] = existing
                    self.resolved_names.add(defn.name)
                    newly_resolved.append(idx)
                    self.resolved_count += 1
                    continue

                # Create new node
                full_oid = f"{parent.full_oid}.{defn.fragment}" if parent.full_oid else str(defn.fragment)
                node = OIDNode(
                    name=defn.name,
                    oid_fragment=defn.fragment,
                    full_oid=full_oid,
                    description=defn.description,
                    syntax=defn.syntax,
                    max_access=defn.max_access,
                    status=defn.status,
                    module=mod_name,
                    deftype=defn.deftype,
                )
                parent.add_child(node)
                self.symbol_table[defn.name] = node
                self.resolved_names.add(defn.name)
                newly_resolved.append(idx)
                self.resolved_count += 1

            if not newly_resolved:
                break

            # Remove resolved from unresolved list
            resolved_set = set(newly_resolved)
            unresolved = [i for i in unresolved if i not in resolved_set]

            if progress_cb:
                progress_cb("resolve", self.resolved_count, self.total_defs)

        # Collect unresolved definitions with reasons
        self.unresolved_defs = []
        for idx in unresolved:
            defn, mod_name = self.all_defs[idx]
            if defn.name not in self.resolved_names:
                self.unresolved_defs.append((defn, mod_name))

        if progress_cb:
            progress_cb("done", self.resolved_count, self.total_defs)

    # --- Cache ---

    _CACHE_FILE = os.path.join(CACHE_DIR, "oidtree.json")
    _LAST_FILE = os.path.join(CACHE_DIR, "last.json")

    @staticmethod
    def get_last_folder() -> str | None:
        """Return the last compiled folder path, or None."""
        try:
            with open(MIBCompiler._LAST_FILE, "r") as f:
                data = json.load(f)
            folder = data.get("folder", "")
            if folder and os.path.isdir(folder):
                return folder
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def _save_last(self, folder: str) -> None:
        os.makedirs(CACHE_DIR, exist_ok=True)
        try:
            with open(self._LAST_FILE, "w") as f:
                json.dump({"folder": os.path.abspath(folder)}, f)
        except OSError:
            pass

    def _purge_cache(self) -> None:
        """Delete existing cache file."""
        try:
            os.remove(self._CACHE_FILE)
        except OSError:
            pass

    def _save_cache(self, folder: str) -> None:
        """Serialize compiled tree to disk (single cache file)."""
        os.makedirs(CACHE_DIR, exist_ok=True)

        def serialize_node(node: OIDNode) -> dict:
            return {
                "n": node.name,
                "f": node.oid_fragment,
                "o": node.full_oid,
                "d": node.description,
                "sy": node.syntax,
                "a": node.max_access,
                "st": node.status,
                "m": node.module,
                "dt": node.deftype,
                "c": [serialize_node(ch) for ch in
                      sorted(node.children.values(), key=lambda x: x.oid_fragment)],
            }

        unresolved_list = []
        for defn, mod_name in self.unresolved_defs:
            unresolved_list.append({
                "name": defn.name,
                "parent": defn.parent_name,
                "frag": defn.fragment,
                "module": mod_name,
                "deftype": defn.deftype,
            })

        data = {
            "folder": os.path.abspath(folder),
            "file_count": self.file_count,
            "module_count": len(self.modules),
            "total_defs": self.total_defs,
            "resolved_count": self.resolved_count,
            "tree": [serialize_node(ch) for ch in
                     sorted(self.root.children.values(), key=lambda x: x.oid_fragment)],
            "unresolved": unresolved_list,
        }

        try:
            with open(self._CACHE_FILE, "w") as f:
                json.dump(data, f, separators=(",", ":"))
        except OSError:
            pass

    def _load_cache(self, folder: str) -> bool:
        """Load compiled tree from cache. Returns True on success."""
        if not os.path.exists(self._CACHE_FILE):
            return False

        try:
            with open(self._CACHE_FILE, "r") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False

        # Cache must be for the same folder
        if os.path.abspath(folder) != data.get("folder"):
            return False

        # Rebuild tree
        self._seed_well_known()

        def deserialize_node(d: dict, parent: OIDNode) -> OIDNode:
            existing = parent.get_child(d["f"])
            if existing:
                if d["d"] and not existing.description:
                    existing.description = d["d"]
                if d["sy"] and not existing.syntax:
                    existing.syntax = d["sy"]
                if d["a"] and not existing.max_access:
                    existing.max_access = d["a"]
                if d["st"] and not existing.status:
                    existing.status = d["st"]
                if d["m"] and existing.module == "(well-known)":
                    existing.module = d["m"]
                node = existing
            else:
                node = OIDNode(
                    name=d["n"],
                    oid_fragment=d["f"],
                    full_oid=d["o"],
                    description=d["d"],
                    syntax=d["sy"],
                    max_access=d["a"],
                    status=d["st"],
                    module=d["m"],
                    deftype=d["dt"],
                )
                parent.add_child(node)

            self.symbol_table[d["n"]] = node

            for child_data in d.get("c", []):
                deserialize_node(child_data, node)
            return node

        for child_data in data["tree"]:
            deserialize_node(child_data, self.root)

        self.file_count = data.get("file_count", 0)
        self.total_defs = data.get("total_defs", 0)
        self.resolved_count = data.get("resolved_count", 0)

        self.unresolved_defs = []
        for u in data.get("unresolved", []):
            defn = RawDefinition(
                name=u["name"],
                parent_name=u["parent"],
                fragment=u["frag"],
                deftype=u.get("deftype", ""),
            )
            self.unresolved_defs.append((defn, u["module"]))

        self._cached_module_count = data.get("module_count", 0)

        return True
