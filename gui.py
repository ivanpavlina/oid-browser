"""tkinter GUI for OID browser."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from models import OIDNode
from compiler import MIBCompiler

_DUMMY = "__dummy__"


class OIDBrowserApp:
    """Main application window."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("OID Browser — SNMP MIB Explorer")
        self.root.geometry("1100x700")

        self.compiler = MIBCompiler()
        self.oid_root: OIDNode | None = None
        # Map treeview item IDs -> OIDNode
        self.tree_nodes: dict[str, OIDNode] = {}
        # Reverse map: OIDNode id() -> treeview iid (for search)
        self.node_to_iid: dict[int, str] = {}
        # For search results
        self.search_results: list[OIDNode] = []
        self.search_idx = 0
        # Currently selected node
        self._selected_node: OIDNode | None = None

        self._build_ui()
        # Auto-load last used folder from cache on startup
        self.root.after(100, self._auto_load_last)

    def _build_ui(self) -> None:
        # === Top bar (pack first — fixed height at top) ===
        top = ttk.Frame(self.root, padding=5)
        top.pack(side=tk.TOP, fill=tk.X)

        self.load_btn = ttk.Button(top, text="Initialize MIB Folder", command=self._on_load)
        self.load_btn.pack(side=tk.LEFT)

        self.unresolved_btn = ttk.Button(top, text="Unresolved...", command=self._on_show_unresolved,
                                          state=tk.DISABLED)
        self.unresolved_btn.pack(side=tk.LEFT, padx=5)

        self.status_var = tk.StringVar(value="No MIBs loaded")
        ttk.Label(top, textvariable=self.status_var).pack(side=tk.LEFT, padx=10)

        self.progress = ttk.Progressbar(top, mode="indeterminate", length=200)
        # Hidden by default — only shown during loading
        self.progress.pack(side=tk.RIGHT)
        self.progress.pack_forget()

        # === Bottom: search bar (pack second — fixed height at bottom, always visible) ===
        bottom = ttk.Frame(self.root, padding=5)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        ttk.Label(bottom, text="Search:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(bottom, textvariable=self.search_var, width=40)
        search_entry.pack(side=tk.LEFT, padx=5)
        search_entry.bind("<Return>", lambda e: self._on_search())

        ttk.Button(bottom, text="Find", command=self._on_search).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Next", command=self._on_search_next).pack(side=tk.LEFT, padx=2)

        self.search_status = tk.StringVar()
        ttk.Label(bottom, textvariable=self.search_status).pack(side=tk.LEFT, padx=10)

        # === Main paned area (pack last — fills remaining space) ===
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        # --- Left: tree ---
        tree_frame = ttk.Frame(paned)
        paned.add(tree_frame, weight=1)

        self.tree = ttk.Treeview(tree_frame, selectmode="browse")
        self.tree.heading("#0", text="OID Tree", anchor=tk.W)

        tree_scroll_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scroll_x = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)

        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        tree_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)

        # --- Right: detail panel with vertical paned window ---
        right_paned = ttk.PanedWindow(paned, orient=tk.VERTICAL)
        paned.add(right_paned, weight=1)

        # Top section: copyable header fields
        header_frame = ttk.LabelFrame(right_paned, text="OID Details", padding=8)
        right_paned.add(header_frame, weight=0)

        self._detail_vars: dict[str, tk.StringVar] = {}
        for label in ("Name", "OID"):
            row = ttk.Frame(header_frame)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=f"{label}:", width=8, anchor=tk.W,
                       font=("TkDefaultFont", 9, "bold")).pack(side=tk.LEFT)
            var = tk.StringVar()
            self._detail_vars[label] = var
            # Copyable entry (read-only)
            entry = ttk.Entry(row, textvariable=var, state="readonly",
                               font=("TkFixedFont", 10))
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
            ttk.Button(row, text="Copy", width=5,
                        command=lambda v=var: self._copy_to_clipboard(v.get())).pack(side=tk.RIGHT)

        # Bottom section: Definition / full MIB text
        desc_frame = ttk.LabelFrame(right_paned, text="Definition", padding=5)
        right_paned.add(desc_frame, weight=1)

        btn_row = ttk.Frame(desc_frame)
        btn_row.pack(fill=tk.X, pady=(0, 3))
        ttk.Button(btn_row, text="Copy All", command=self._copy_description).pack(side=tk.RIGHT)

        self.desc_text = tk.Text(desc_frame, wrap=tk.WORD, state=tk.DISABLED,
                                  font=("TkFixedFont", 10), padx=5, pady=5)
        desc_scroll = ttk.Scrollbar(desc_frame, orient=tk.VERTICAL, command=self.desc_text.yview)
        self.desc_text.configure(yscrollcommand=desc_scroll.set)
        desc_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.desc_text.pack(fill=tk.BOTH, expand=True)

    # --- Clipboard ---

    def _copy_to_clipboard(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _copy_description(self) -> None:
        text = self.desc_text.get("1.0", tk.END).strip()
        if text:
            self._copy_to_clipboard(text)

    # --- Loading ---

    def _auto_load_last(self) -> None:
        """On startup, auto-load from cache if a previous folder was used."""
        folder = MIBCompiler.get_last_folder()
        if folder:
            self._start_compile(folder, force=False)

    def _on_load(self) -> None:
        """Initialize MIB Folder — always forces a full recompile."""
        folder = filedialog.askdirectory(title="Select MIBs folder")
        if not folder:
            return
        self._start_compile(folder, force=True)

    def _start_compile(self, folder: str, force: bool) -> None:
        self.load_btn.configure(state=tk.DISABLED)
        self.unresolved_btn.configure(state=tk.DISABLED)
        self.progress.pack(side=tk.RIGHT)
        self.progress.start(10)
        self.status_var.set("Loading..." if not force else "Compiling...")

        # Clear existing tree
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.tree_nodes.clear()
        self.node_to_iid.clear()

        thread = threading.Thread(target=self._compile_thread, args=(folder, force), daemon=True)
        thread.start()

    def _compile_thread(self, folder: str, force: bool) -> None:
        def progress_cb(phase: str, current: int, total: int) -> None:
            if phase == "cache":
                msg = "Loading cache..."
            elif phase == "scan":
                msg = f"Found {current} files..."
            elif phase == "parse":
                msg = f"Parsing: {current}/{total} files..."
            elif phase == "resolve":
                msg = f"Resolving: {current}/{total} OIDs..."
            else:
                msg = f"Done: {current}/{total} OIDs resolved"
            self.root.after(0, lambda m=msg: self.status_var.set(m))

        compiler = MIBCompiler()
        oid_root, resolved, total = compiler.compile_folder(folder, progress_cb, force=force)
        self.compiler = compiler
        self.oid_root = oid_root

        self.root.after(0, self._on_compile_done, oid_root, resolved, total)

    def _on_compile_done(self, oid_root: OIDNode, resolved: int, total: int) -> None:
        self.progress.stop()
        self.progress.pack_forget()
        self.load_btn.configure(state=tk.NORMAL)

        unresolved = total - resolved
        module_count = len(self.compiler.modules) or getattr(self.compiler, '_cached_module_count', 0)
        self.status_var.set(
            f"{resolved}/{total} OIDs resolved | "
            f"{unresolved} unresolved | "
            f"{self.compiler.file_count} files | "
            f"{module_count} modules"
        )

        if unresolved > 0:
            self.unresolved_btn.configure(state=tk.NORMAL)

        self._populate_tree(oid_root)

    # --- Unresolved popup ---

    def _on_show_unresolved(self) -> None:
        unresolved = self.compiler.unresolved_defs
        if not unresolved:
            messagebox.showinfo("Unresolved OIDs", "All OIDs resolved successfully.")
            return

        win = tk.Toplevel(self.root)
        win.title(f"Unresolved OIDs ({len(unresolved)})")
        win.geometry("800x500")
        win.transient(self.root)

        # Summary by reason
        missing_parents: dict[str, list[tuple[str, str]]] = {}  # parent -> [(name, module)]
        for defn, mod_name in unresolved:
            missing_parents.setdefault(defn.parent_name, []).append((defn.name, mod_name))

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=f"Total unresolved: {len(unresolved)} definitions",
                   font=("TkDefaultFont", 10, "bold")).pack(anchor=tk.W)
        ttk.Label(frame, text=f"Missing {len(missing_parents)} parent symbols",
                   font=("TkDefaultFont", 9)).pack(anchor=tk.W, pady=(0, 5))

        # Treeview: missing parent -> children that need it
        cols = ("count", "example_child", "example_module")
        tv = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        tv.heading("count", text="# Children")
        tv.heading("example_child", text="Example Definition")
        tv.heading("example_module", text="Module")
        tv.column("count", width=80, anchor=tk.CENTER)
        tv.column("example_child", width=300)
        tv.column("example_module", width=200)

        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tv.yview)
        tv.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tv.pack(fill=tk.BOTH, expand=True)

        # Sort by count descending
        sorted_parents = sorted(missing_parents.items(), key=lambda x: -len(x[1]))
        for parent_name, children in sorted_parents:
            example_name, example_mod = children[0]
            tv.insert("", tk.END, values=(len(children), f"{example_name} (needs: {parent_name})", example_mod))

        # Detail text at bottom
        detail = tk.Text(frame, height=8, wrap=tk.WORD, font=("TkFixedFont", 9))
        detail.pack(fill=tk.X, pady=(5, 0))
        detail.insert("1.0",
            "Unresolved definitions are those whose parent symbol could not be found\n"
            "in the OID tree. Common reasons:\n\n"
            "  - The MIB file defining the parent is missing from the folder\n"
            "  - The parent MIB has a parse error (non-standard syntax)\n"
            "  - Circular or unresolvable dependency chains\n"
            "  - Vendor-specific MIBs referencing proprietary base MIBs\n"
        )
        detail.configure(state=tk.DISABLED)

    # --- Tree population (lazy) ---

    def _populate_tree(self, oid_root: OIDNode) -> None:
        """Populate top-level nodes with lazy children."""
        self.tree_nodes.clear()
        self.node_to_iid.clear()

        for child in sorted(oid_root.children.values(), key=lambda n: n.oid_fragment):
            self._insert_lazy("", child)

    def _insert_lazy(self, parent_iid: str, node: OIDNode) -> str:
        """Insert a node with a dummy child if it has real children."""
        label = f"{node.name} ({node.oid_fragment})"
        iid = self.tree.insert(parent_iid, tk.END, text=label, open=False)
        self.tree_nodes[iid] = node
        self.node_to_iid[id(node)] = iid

        if node.children:
            # Insert dummy so the expand arrow appears
            self.tree.insert(iid, tk.END, text="", tags=(_DUMMY,))

        return iid

    def _on_tree_open(self, _event) -> None:
        """Lazy-load children when a node is expanded."""
        iid = self.tree.focus()
        if iid:
            self._expand_iid(iid)

    def _expand_iid(self, iid: str) -> None:
        """Ensure children of iid are loaded (replace dummy with real children)."""
        children = self.tree.get_children(iid)
        if len(children) == 1:
            tags = self.tree.item(children[0], "tags")
            if tags and _DUMMY in tags:
                self.tree.delete(children[0])
                node = self.tree_nodes.get(iid)
                if node:
                    for child in sorted(node.children.values(), key=lambda n: n.oid_fragment):
                        self._insert_lazy(iid, child)

    # --- Selection ---

    def _on_tree_select(self, _event) -> None:
        sel = self.tree.selection()
        if not sel:
            return

        node = self.tree_nodes.get(sel[0])
        if not node:
            return

        self._selected_node = node
        self._detail_vars["Name"].set(node.name)
        self._detail_vars["OID"].set(node.full_oid)

        # Build MIB-formatted definition text
        self.desc_text.configure(state=tk.NORMAL)
        self.desc_text.delete("1.0", tk.END)
        self.desc_text.insert("1.0", self._format_mib_definition(node))
        self.desc_text.configure(state=tk.DISABLED)

    @staticmethod
    def _format_mib_definition(node: OIDNode) -> str:
        """Format node as a classic MIB definition block."""
        lines = []

        # Header line: name DEFTYPE
        deftype = node.deftype or "OBJECT IDENTIFIER"
        lines.append(f"{node.name} {deftype}")

        # SYNTAX
        if node.syntax:
            lines.append(f"    SYNTAX      {node.syntax}")

        # MAX-ACCESS
        if node.max_access:
            lines.append(f"    MAX-ACCESS  {node.max_access}")

        # STATUS
        if node.status:
            lines.append(f"    STATUS      {node.status}")

        # DESCRIPTION
        if node.description:
            lines.append(f'    DESCRIPTION')
            lines.append(f'        "{node.description}"')

        # ::= assignment
        parent_name = node.parent.name if node.parent else "?"
        lines.append(f"    ::= {{ {parent_name} {node.oid_fragment} }}")

        # Separator + metadata
        lines.append("")
        lines.append(f"-- Full OID:  {node.full_oid}")
        lines.append(f"-- Module:    {node.module}")
        if node.children:
            child_names = ", ".join(
                ch.name for ch in sorted(node.children.values(),
                                          key=lambda n: n.oid_fragment)[:15]
            )
            if len(node.children) > 15:
                child_names += f", ... ({len(node.children)} total)"
            lines.append(f"-- Children:  {child_names}")

        return "\n".join(lines)

    # --- Search ---

    def _on_search(self) -> None:
        query = self.search_var.get().strip().lower()
        if not query:
            return

        self.search_results = []
        self.search_idx = 0

        # Search through the symbol table (fast, doesn't need treeview to be expanded)
        seen: set[int] = set()
        name_matches = []
        oid_matches = []
        desc_matches = []
        for name, node in self.compiler.symbol_table.items():
            nid = id(node)
            if nid in seen:
                continue
            if query in name.lower():
                seen.add(nid)
                name_matches.append(node)
            elif query in node.full_oid:
                seen.add(nid)
                oid_matches.append(node)
            elif query in node.description.lower():
                seen.add(nid)
                desc_matches.append(node)

        # Prioritize: exact name > name contains > OID contains > description contains
        name_matches.sort(key=lambda n: (n.name.lower() != query, n.full_oid))
        oid_matches.sort(key=lambda n: n.full_oid)
        desc_matches.sort(key=lambda n: n.full_oid)
        self.search_results = name_matches + oid_matches + desc_matches

        if self.search_results:
            count = len(self.search_results)
            self.search_status.set(f"1/{count} matches")
            self._reveal_and_select(self.search_results[0])
        else:
            self.search_status.set("No matches")

    def _on_search_next(self) -> None:
        if not self.search_results:
            return
        self.search_idx = (self.search_idx + 1) % len(self.search_results)
        count = len(self.search_results)
        self.search_status.set(f"{self.search_idx + 1}/{count} matches")
        self._reveal_and_select(self.search_results[self.search_idx])

    def _reveal_and_select(self, node: OIDNode) -> None:
        """Ensure the node's path is expanded in the treeview, then select it."""
        # Build the path from root to this node
        path: list[OIDNode] = []
        n = node
        while n and n.parent:
            path.append(n)
            n = n.parent
        path.reverse()

        # Expand each ancestor to ensure the node exists in the treeview
        for ancestor in path[:-1]:  # all except the target itself
            iid = self.node_to_iid.get(id(ancestor))
            if iid:
                self._expand_iid(iid)
                self.tree.item(iid, open=True)

        # Now the target should exist
        target_iid = self.node_to_iid.get(id(node))
        if target_iid:
            self.tree.selection_set(target_iid)
            self.tree.see(target_iid)
            self.tree.focus(target_iid)
