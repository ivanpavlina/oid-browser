"""Data models for OID browser."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawDefinition:
    """An unresolved OID definition extracted from a MIB file."""
    name: str
    parent_name: str
    fragment: int
    description: str = ""
    syntax: str = ""
    max_access: str = ""
    status: str = ""
    deftype: str = ""  # OBJECT-TYPE, MODULE-IDENTITY, OBJECT IDENTIFIER, etc.


@dataclass
class OIDNode:
    """A node in the resolved OID tree."""
    name: str
    oid_fragment: int
    full_oid: str = ""
    description: str = ""
    syntax: str = ""
    max_access: str = ""
    status: str = ""
    module: str = ""
    deftype: str = ""
    parent: Optional[OIDNode] = field(default=None, repr=False)
    children: dict[int, OIDNode] = field(default_factory=dict)

    def get_child(self, fragment: int) -> Optional[OIDNode]:
        return self.children.get(fragment)

    def add_child(self, node: OIDNode) -> None:
        node.parent = self
        self.children[node.oid_fragment] = node

    def walk(self):
        """Yield all nodes in depth-first order."""
        yield self
        for child in sorted(self.children.values(), key=lambda n: n.oid_fragment):
            yield from child.walk()


@dataclass
class MIBModule:
    """A parsed but potentially unresolved MIB module."""
    name: str
    filename: str
    imports: dict[str, list[str]] = field(default_factory=dict)
    definitions: list[RawDefinition] = field(default_factory=list)
    parsed: bool = False
    resolved: bool = False
