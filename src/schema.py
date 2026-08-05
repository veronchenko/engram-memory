"""
Entry schema — the backend's machine-readable validation contract.

`schema.json` declares the entry taxonomy and the per-type rules code is
allowed to enforce (required frontmatter fields, required body fields,
membership, ranking behaviour). No entry type name is hardcoded in Python:
every per-type behaviour is a field read from here.

Resolution order, first found wins: `<data-path>/schema.json`, then the
packaged `schema.json` next to this module. A user schema fully REPLACES
the packaged one — partially overridden types would be a bug class with no
benefit. The schema is read once at startup because `entry_type` is exposed
as an enum in the MCP tool signature, so editing it requires a restart.

The companion document `ENGRAM_TEMPLATES.md` is a hand-written spec for the
LLM (wording, examples, type-choice nuance) and is deliberately NOT
generated from this file, nor this file from it.
"""

from __future__ import annotations

import enum
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger("engram")

# Filename looked up both in the data path and next to this module
SCHEMA_FILENAME: Final[str] = "schema.json"

# Packaged default, shipped with the code
PACKAGED_SCHEMA_PATH: Final[Path] = Path(__file__).parent / SCHEMA_FILENAME

# How a type expresses project membership. Parsed and carried today;
# enforcement lands with the `part_of` frontmatter field.
MEMBERSHIP_VALUES: Final[frozenset[str]] = frozenset({"required", "optional", "none"})

# Fallback when a schema omits `limits.max_degree`
DEFAULT_MAX_DEGREE: Final[int] = 15

# Edge semantics assumed on a kb:// link that carries no `:edge` suffix.
# Every schema must allow it, since most links omit the suffix.
DEFAULT_EDGE: Final[str] = "related_to"

# Fallback when a schema omits `edges`. Deliberately small: `supersedes`
# is NOT here — versioning already has a first-class mechanism
# (remember(supersede=True)) and must not be re-expressible as a link.
DEFAULT_EDGES: Final[tuple[str, ...]] = ("supports", "contradicts", DEFAULT_EDGE)


class SchemaError(ValueError):
    """Raised when a schema file is missing, unreadable, or malformed."""


@dataclass(frozen=True)
class TypeRule:
    """
    Rules declared for a single entry type.

    Attributes:
        required: Frontmatter fields that must be present and non-empty.
        body: Bold field labels that must appear in the body as
            `**<label>:**` (the form the templates already use).
        membership: How the type expresses project membership —
            'required', 'optional', or 'none'.
        membership_target: Whether entries of this type may be the
            target of another entry's `part_of` field. True for hubs —
            membership means belonging to a project, and hubs are the
            entries that stand for projects.
        usage_boost: Whether search may boost this type by access count.
            False for hubs: they are read at the start of every session,
            so the boost is a systematic ranking skew rather than a signal.
        digest_on_recall: Whether `recall` replaces the flat incoming
            relation list with a per-type digest (for high-degree types).
    """

    required: tuple[str, ...] = ()
    body: tuple[str, ...] = ()
    membership: str = "optional"
    membership_target: bool = False
    usage_boost: bool = True
    digest_on_recall: bool = False


@dataclass(frozen=True)
class Schema:
    """
    The full validation contract loaded from a schema.json file.

    Attributes:
        version: Schema format version.
        types: Entry type name -> its rules.
        edges: Allowed kb:// edge semantics. Never empty — a schema that
            declares none gets DEFAULT_EDGES.
        max_degree: Node degree above which `doctor` reports a supernode.
        source: Path the schema was loaded from (for diagnostics).
    """

    version: int
    types: Mapping[str, TypeRule]
    edges: tuple[str, ...] = DEFAULT_EDGES
    max_degree: int = DEFAULT_MAX_DEGREE
    source: Path | None = field(default=None, compare=False)

    @property
    def edge_types(self) -> frozenset[str]:
        """The allowed edge semantics, as a set for membership tests."""

        # Link parsing checks membership, not order
        return frozenset(self.edges)

    def rule(self, entry_type: str) -> TypeRule | None:
        """
        Look up the rules for an entry type.

        Args:
            entry_type: Type name as written in the entry's frontmatter.

        Returns:
            The TypeRule, or None when the type is not declared.
        """

        # Unknown types have no rules — `doctor` reports them separately
        return self.types.get(entry_type)

    def types_where(self, attribute: str, value: Any) -> frozenset[str]:
        """
        Names of the types whose rule attribute equals a given value.

        Lets callers ask "which types skip the usage boost?" without
        naming a single type in code.

        Args:
            attribute: TypeRule attribute name.
            value: Value to match.

        Returns:
            Frozen set of matching type names.

        Raises:
            AttributeError: If the attribute is not a TypeRule field.
        """

        return frozenset(
            name for name, rule in self.types.items() if getattr(rule, attribute) == value
        )


def _as_str_tuple(raw: Any, context: str) -> tuple[str, ...]:
    """
    Coerce a JSON value to a tuple of non-empty strings.

    Args:
        raw: Value from the parsed JSON.
        context: Dotted path used in error messages.

    Returns:
        Tuple of strings (empty when raw is absent).

    Raises:
        SchemaError: If the value is not a list of strings.
    """

    if raw is None:
        # Absent is equivalent to empty
        return ()

    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise SchemaError(f"{context} must be a list of strings")

    return tuple(item for item in raw if item.strip())


def _parse_type_rule(name: str, raw: Any) -> TypeRule:
    """
    Build a TypeRule from one entry of the schema's `types` object.

    Args:
        name: Type name (used in error messages).
        raw: Raw JSON object for the type.

    Returns:
        Parsed TypeRule with defaults applied for omitted keys.

    Raises:
        SchemaError: If the rule is not an object or a field has the
            wrong type / an unknown membership value.
    """

    if not isinstance(raw, dict):
        raise SchemaError(f"types.{name} must be an object")

    membership = raw.get("membership", "optional")
    if membership not in MEMBERSHIP_VALUES:
        raise SchemaError(
            f"types.{name}.membership must be one of "
            f"{sorted(MEMBERSHIP_VALUES)}, got {membership!r}"
        )

    for flag in ("membership_target", "usage_boost", "digest_on_recall"):
        if flag in raw and not isinstance(raw[flag], bool):
            raise SchemaError(f"types.{name}.{flag} must be a boolean")

    return TypeRule(
        required=_as_str_tuple(raw.get("required"), f"types.{name}.required"),
        body=_as_str_tuple(raw.get("body"), f"types.{name}.body"),
        membership=membership,
        membership_target=bool(raw.get("membership_target", False)),
        usage_boost=bool(raw.get("usage_boost", True)),
        digest_on_recall=bool(raw.get("digest_on_recall", False)),
    )


def parse_schema(raw: Any, source: Path | None = None) -> Schema:
    """
    Validate and convert a parsed JSON document into a Schema.

    Args:
        raw: The parsed JSON document.
        source: Path it came from, kept for diagnostics.

    Returns:
        The validated Schema.

    Raises:
        SchemaError: If the document is malformed — the server fails to
            start rather than silently running on a half-understood
            contract.
    """

    if not isinstance(raw, dict):
        raise SchemaError("schema must be a JSON object")

    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise SchemaError("schema.version must be an integer")

    raw_types = raw.get("types")
    if not isinstance(raw_types, dict) or not raw_types:
        raise SchemaError("schema.types must be a non-empty object")

    limits = raw.get("limits", {})
    if not isinstance(limits, dict):
        raise SchemaError("schema.limits must be an object")

    max_degree = limits.get("max_degree", DEFAULT_MAX_DEGREE)
    if not isinstance(max_degree, int) or isinstance(max_degree, bool):
        raise SchemaError("schema.limits.max_degree must be an integer")

    edges = _as_str_tuple(raw.get("edges"), "schema.edges") or DEFAULT_EDGES
    if DEFAULT_EDGE not in edges:
        # Links without a `:edge` suffix fall back to DEFAULT_EDGE, so a
        # vocabulary that omits it could never describe most of the graph
        raise SchemaError(f"schema.edges must include the default '{DEFAULT_EDGE}'")

    return Schema(
        version=version,
        types={name: _parse_type_rule(name, rule) for name, rule in raw_types.items()},
        edges=edges,
        max_degree=max_degree,
        source=source,
    )


def _read_schema_file(path: Path) -> Schema:
    """
    Read and parse a single schema file.

    Args:
        path: Path to the JSON file.

    Returns:
        The parsed Schema.

    Raises:
        SchemaError: If the file cannot be read or is not valid JSON.
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SchemaError(f"Cannot read schema {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SchemaError(f"Invalid JSON in schema {path}: {exc}") from exc

    return parse_schema(raw, source=path)


def load_schema(data_path: str | Path | None = None) -> Schema:
    """
    Load the entry schema, preferring a user override in the data path.

    Resolution order, first found wins: `<data-path>/schema.json`, then
    the packaged `schema.json`. A user file replaces the packaged one
    outright — the two are never merged.

    Args:
        data_path: Knowledge base root. When None, only the packaged
            schema is considered.

    Returns:
        The loaded Schema.

    Raises:
        SchemaError: If the resolved file is unreadable or malformed, or
            if the packaged schema is missing.
    """

    if data_path is not None:
        override = Path(data_path) / SCHEMA_FILENAME
        if override.exists():
            schema = _read_schema_file(override)
            logger.info(
                "Loaded entry schema v%d from %s (%d types)",
                schema.version,
                override,
                len(schema.types),
            )
            # User override wins outright
            return schema

    if not PACKAGED_SCHEMA_PATH.exists():
        raise SchemaError(f"Packaged schema missing at {PACKAGED_SCHEMA_PATH}")

    schema = _read_schema_file(PACKAGED_SCHEMA_PATH)
    logger.info(
        "Loaded packaged entry schema v%d (%d types)",
        schema.version,
        len(schema.types),
    )
    return schema


def build_entry_type_enum(type_names: Sequence[str]) -> type[enum.Enum]:
    """
    Build the `entry_type` enum exposed in the `remember` tool signature.

    FastMCP derives the tool's JSON Schema from type hints, so declaring
    the parameter as this enum makes an invalid type impossible to send:
    the client rejects the call before it reaches any code. `entry_type`
    is the only convention in the KB with 100% adoption precisely because
    `remember` fails without it — this raises the bar from "field present"
    to "value correct".

    Args:
        type_names: Type names from the schema, in declaration order.

    Returns:
        A str-valued Enum class whose members are the schema's types.

    Raises:
        SchemaError: If there are no type names to build from.
    """

    if not type_names:
        raise SchemaError("Cannot build entry_type enum from an empty schema")

    # str base so the value serializes as a plain string in JSON Schema
    return enum.Enum(  # type: ignore[return-value]
        "EntryType", {name: name for name in type_names}, type=str
    )
