"""Single-file MIB parser. Extracts module name, imports, and OID definitions."""

from __future__ import annotations

import re
from models import MIBModule, RawDefinition


def parse_mib_file(filepath: str) -> list[MIBModule]:
    """Parse a MIB file and return a list of MIBModule objects.

    Returns a list because a single file can theoretically contain
    multiple MODULE DEFINITIONS, though usually just one.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []

    # Strip single-line comments (-- to end of line), but not inside quoted strings
    text = _strip_comments(text)

    modules = []
    # Split on module boundaries
    # Pattern: NAME DEFINITIONS ::= BEGIN ... END
    module_pattern = re.compile(
        r'(\S+)\s+DEFINITIONS\s*::=\s*BEGIN\s*(.*?)\bEND\b',
        re.DOTALL
    )

    for match in module_pattern.finditer(text):
        mod_name = match.group(1)
        mod_body = match.group(2)

        module = MIBModule(name=mod_name, filename=filepath, parsed=True)
        module.imports = _parse_imports(mod_body)
        module.definitions = _parse_definitions(mod_body)
        modules.append(module)

    return modules


def _strip_comments(text: str) -> str:
    """Remove -- comments while preserving strings."""
    result = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '"':
            # Inside a quoted string — skip to closing quote
            j = i + 1
            while j < n and text[j] != '"':
                j += 1
            result.append(text[i:j + 1])
            i = j + 1
        elif i < n - 1 and text[i] == '-' and text[i + 1] == '-':
            # Comment — skip to end of line
            j = i + 2
            while j < n and text[j] != '\n':
                j += 1
            # Keep the newline
            result.append('\n')
            i = j
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)


def _parse_imports(body: str) -> dict[str, list[str]]:
    """Extract IMPORTS section -> {module_name: [symbol_names]}."""
    imports: dict[str, list[str]] = {}

    imp_match = re.search(r'\bIMPORTS\b(.*?);', body, re.DOTALL)
    if not imp_match:
        return imports

    imp_text = imp_match.group(1)

    # Format: symbol1, symbol2 FROM ModuleName symbol3 FROM ModuleName2
    # Split on FROM
    parts = re.split(r'\bFROM\b', imp_text)
    for i in range(1, len(parts)):
        # parts[i] starts with module_name, followed by symbols for the NEXT import
        # parts[i-1] has the symbols for THIS import
        mod_line = parts[i]

        # Module name is the first word
        mod_match = re.match(r'\s*([\w-]+)', mod_line)
        if not mod_match:
            continue
        mod_name = mod_match.group(1).rstrip(';')

        # Symbols: for the first FROM (i==1), use all of parts[0].
        # For subsequent FROMs, strip the leading module name from parts[i-1].
        symbols_text = parts[i - 1]
        if i > 1:
            # Remove the leading module name (from the previous FROM clause)
            symbols_text = re.sub(r'^\s*[\w-]+', '', symbols_text, count=1)

        syms = re.findall(r'([\w-]+)', symbols_text)
        if syms:
            imports[mod_name] = syms

    return imports


def _parse_definitions(body: str) -> list[RawDefinition]:
    """Extract OID definitions from module body."""
    defs: list[RawDefinition] = []

    # 1. Simple OID assignments: name OBJECT IDENTIFIER ::= { parent id }
    for m in re.finditer(
        r'(\w[\w-]*)\s+OBJECT\s+IDENTIFIER\s*::=\s*\{([^}]+)\}',
        body
    ):
        name = m.group(1)
        parent_name, fragment = _parse_oid_value(m.group(2))
        if parent_name and fragment is not None:
            defs.append(RawDefinition(
                name=name, parent_name=parent_name, fragment=fragment,
                deftype="OBJECT IDENTIFIER"
            ))

    # 2. MODULE-IDENTITY, OBJECT-TYPE, OBJECT-IDENTITY,
    #    MODULE-COMPLIANCE, OBJECT-GROUP, NOTIFICATION-TYPE, NOTIFICATION-GROUP
    #    name TYPE-KEYWORD ... ::= { parent id }
    macro_types = (
        'MODULE-IDENTITY', 'OBJECT-TYPE', 'OBJECT-IDENTITY',
        'MODULE-COMPLIANCE', 'OBJECT-GROUP', 'NOTIFICATION-TYPE',
        'NOTIFICATION-GROUP', 'AGENT-CAPABILITIES',
    )
    macro_pat = '|'.join(re.escape(m) for m in macro_types)

    # We need to find: name MACRO-TYPE ... ::= { parent id }
    # This is tricky because the ... can span many lines.
    # Strategy: find each "name MACRO-TYPE" then scan forward for ::= { ... }
    macro_re = re.compile(
        r'^[ \t]*([\w][\w-]*)[ \t]+(' + macro_pat + r')\b',
        re.MULTILINE,
    )

    for m in macro_re.finditer(body):
        name = m.group(1)
        deftype = m.group(2)

        # Skip if this name was already found as OBJECT IDENTIFIER
        if any(d.name == name for d in defs):
            continue

        # Scan forward from this position for ::= { parent id }
        rest = body[m.start():]

        # Find the ::= { ... } assignment
        assign_match = re.search(r'::=\s*\{([^}]+)\}', rest)
        if not assign_match:
            continue

        parent_name, fragment = _parse_oid_value(assign_match.group(1))
        if not parent_name or fragment is None:
            continue

        # Extract DESCRIPTION (between the macro keyword and the ::=)
        block = rest[:assign_match.start()]
        description = _extract_description(block)
        syntax = _extract_field(block, 'SYNTAX')
        max_access = _extract_field(block, 'MAX-ACCESS') or _extract_field(block, 'ACCESS')
        status = _extract_field(block, 'STATUS')

        defs.append(RawDefinition(
            name=name, parent_name=parent_name, fragment=fragment,
            description=description, syntax=syntax,
            max_access=max_access, status=status, deftype=deftype,
        ))

    return defs


def _parse_oid_value(value_str: str) -> tuple[str | None, int | None]:
    """Parse '{ parent id }' contents -> (parent_name, fragment).

    Handles:
      { parent 42 }
      { iso 3 }
      { parent childName(42) }  -- named numbers
    """
    tokens = value_str.strip().split()
    if len(tokens) < 2:
        return None, None

    parent = tokens[0]

    # Last token is the numeric fragment, possibly as name(N)
    last = tokens[-1]
    num_match = re.search(r'(\d+)', last)
    if num_match:
        return parent, int(num_match.group(1))

    return None, None


def _extract_description(block: str) -> str:
    """Extract DESCRIPTION "..." from a block of text."""
    # Find DESCRIPTION followed by a quoted string (possibly multiline)
    match = re.search(r'\bDESCRIPTION\s*"', block)
    if not match:
        return ""

    start = match.end()  # position after opening "
    # Find the closing " — handle embedded quotes as ""
    i = start
    n = len(block)
    result = []
    while i < n:
        if block[i] == '"':
            # Check for escaped/doubled quote
            if i + 1 < n and block[i + 1] == '"':
                result.append('"')
                i += 2
            else:
                break
        else:
            result.append(block[i])
            i += 1

    desc = ''.join(result)
    # Clean up whitespace: collapse runs of whitespace but preserve paragraph breaks
    lines = desc.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        cleaned.append(stripped)
    desc = '\n'.join(cleaned)
    # Collapse multiple blank lines
    desc = re.sub(r'\n{3,}', '\n\n', desc)
    return desc.strip()


def _extract_field(block: str, field_name: str) -> str:
    """Extract a simple field value like SYNTAX Integer32 or STATUS current."""
    # Match FIELD-NAME followed by value up to next keyword or end
    pattern = re.compile(
        r'\b' + re.escape(field_name) + r'\s+(.*?)(?=\b(?:SYNTAX|MAX-ACCESS|ACCESS|STATUS|DESCRIPTION|REFERENCE|INDEX|AUGMENTS|DEFVAL|OBJECTS|NOTIFICATIONS|DISPLAY-HINT|::=)\b|\Z)',
        re.DOTALL
    )
    match = pattern.search(block)
    if not match:
        return ""
    value = match.group(1).strip()
    # Collapse whitespace
    value = re.sub(r'\s+', ' ', value)
    return value
