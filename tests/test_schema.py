"""
Tests for schema.py — schema resolution, validation, and the entry_type enum.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from schema import (
    DEFAULT_EDGE,
    DEFAULT_EDGES,
    DEFAULT_MAX_DEGREE,
    PACKAGED_SCHEMA_PATH,
    SchemaError,
    build_entry_type_enum,
    load_schema,
    parse_schema,
)


def _write_schema(path: Path, document: dict) -> None:
    """Write a schema document into a data path."""

    (path / "schema.json").write_text(json.dumps(document), encoding="utf-8")


# ===========================================================================
# Packaged schema
# ===========================================================================


class TestPackagedSchema:
    """The schema shipped with the code must be valid and complete."""

    def test_packaged_schema_exists(self) -> None:
        """The packaged schema.json is present next to the module."""

        assert PACKAGED_SCHEMA_PATH.exists()

    def test_packaged_schema_loads(self) -> None:
        """Loading without a data path yields the packaged schema."""

        schema = load_schema(None)

        assert schema.version == 1
        assert "hub" in schema.types
        assert schema.source == PACKAGED_SCHEMA_PATH

    def test_hub_rules(self) -> None:
        """Hub carries the two behaviours code reads instead of hardcoding."""

        schema = load_schema(None)
        hub = schema.types["hub"]

        assert hub.usage_boost is False
        assert hub.digest_on_recall is True
        assert "resource" in hub.required

    def test_types_where(self) -> None:
        """types_where selects type names by rule attribute."""

        schema = load_schema(None)

        assert schema.types_where("usage_boost", False) == frozenset({"hub"})
        assert schema.types_where("digest_on_recall", True) == frozenset({"hub"})


# ===========================================================================
# Resolution order
# ===========================================================================


class TestResolution:
    """A user schema in the data path fully replaces the packaged one."""

    def test_data_path_override_wins(self, tmp_path: Path) -> None:
        """A schema.json in the data path is preferred."""

        _write_schema(tmp_path, {"version": 7, "types": {"note": {}}})

        schema = load_schema(tmp_path)

        assert schema.version == 7
        assert set(schema.types) == {"note"}

    def test_override_is_replacement_not_merge(self, tmp_path: Path) -> None:
        """Packaged types do not leak into a user schema."""

        _write_schema(tmp_path, {"version": 1, "types": {"note": {}}})

        schema = load_schema(tmp_path)

        assert "hub" not in schema.types
        assert "decision" not in schema.types

    def test_falls_back_to_packaged(self, tmp_path: Path) -> None:
        """Without an override, the packaged schema is used."""

        schema = load_schema(tmp_path)

        assert schema.source == PACKAGED_SCHEMA_PATH


# ===========================================================================
# Validation — fail fast, never run on a half-understood contract
# ===========================================================================


class TestValidation:
    """Malformed schemas raise instead of degrading silently."""

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        """A syntactically broken file raises SchemaError."""

        (tmp_path / "schema.json").write_text("{not json", encoding="utf-8")

        with pytest.raises(SchemaError):
            load_schema(tmp_path)

    def test_missing_version_raises(self) -> None:
        """version is mandatory from day one."""

        with pytest.raises(SchemaError):
            parse_schema({"types": {"note": {}}})

    def test_empty_types_raises(self) -> None:
        """A schema with no types is meaningless."""

        with pytest.raises(SchemaError):
            parse_schema({"version": 1, "types": {}})

    def test_unknown_membership_raises(self) -> None:
        """membership is a closed set."""

        with pytest.raises(SchemaError):
            parse_schema(
                {"version": 1, "types": {"note": {"membership": "sometimes"}}}
            )

    def test_non_boolean_flag_raises(self) -> None:
        """Behaviour flags must be booleans."""

        with pytest.raises(SchemaError):
            parse_schema({"version": 1, "types": {"note": {"usage_boost": "yes"}}})

    def test_body_must_be_string_list(self) -> None:
        """body is a list of labels."""

        with pytest.raises(SchemaError):
            parse_schema({"version": 1, "types": {"note": {"body": [1, 2]}}})


class TestDefaults:
    """Omitted keys fall back to permissive defaults."""

    def test_type_rule_defaults(self) -> None:
        """A bare type declaration has no rules and is boosted normally."""

        schema = parse_schema({"version": 1, "types": {"note": {}}})
        rule = schema.types["note"]

        assert rule.required == ()
        assert rule.body == ()
        assert rule.membership == "optional"
        assert rule.usage_boost is True
        assert rule.digest_on_recall is False

    def test_max_degree_default(self) -> None:
        """Omitting limits falls back to the module default."""

        schema = parse_schema({"version": 1, "types": {"note": {}}})

        assert schema.max_degree == DEFAULT_MAX_DEGREE

    def test_rule_lookup_of_unknown_type(self) -> None:
        """An undeclared type simply has no rules."""

        schema = parse_schema({"version": 1, "types": {"note": {}}})

        assert schema.rule("nonexistent") is None


class TestEdgeVocabulary:
    """`edges` is a contract the link parser reads, not documentation."""

    def test_defaults_when_absent(self) -> None:
        """A schema declaring no edges gets the built-in vocabulary."""

        schema = parse_schema({"version": 1, "types": {"note": {}}})

        assert schema.edge_types == frozenset(DEFAULT_EDGES)

    def test_custom_vocabulary_is_kept(self) -> None:
        """A declared vocabulary replaces the default outright."""

        schema = parse_schema(
            {
                "version": 1,
                "types": {"note": {}},
                "edges": ["refines", DEFAULT_EDGE],
            }
        )

        assert schema.edge_types == frozenset({"refines", DEFAULT_EDGE})

    def test_vocabulary_must_include_the_default_edge(self) -> None:
        """Links without a :edge suffix fall back to it, so it must exist."""

        with pytest.raises(SchemaError, match=DEFAULT_EDGE):
            parse_schema(
                {"version": 1, "types": {"note": {}}, "edges": ["supports"]}
            )


# ===========================================================================
# entry_type enum
# ===========================================================================


class TestEntryTypeEnum:
    """The enum baked into the remember tool signature."""

    def test_members_match_schema(self) -> None:
        """Every schema type becomes an enum member."""

        enum_cls = build_entry_type_enum(("hub", "idea"))

        assert {member.value for member in enum_cls} == {"hub", "idea"}

    def test_members_are_strings(self) -> None:
        """Members compare equal to their string value."""

        enum_cls = build_entry_type_enum(("idea",))

        assert enum_cls("idea") == "idea"

    def test_empty_schema_raises(self) -> None:
        """There is nothing to constrain when no types are declared."""

        with pytest.raises(SchemaError):
            build_entry_type_enum(())
