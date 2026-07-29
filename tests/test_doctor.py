"""
Tests for doctor.py — schema-driven integrity checks over Markdown entries.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from doctor import MAX_REPORTED_IDS, check_entry, run_doctor
from schema import Schema, load_schema, parse_schema


def _entry(**overrides: Any) -> dict[str, Any]:
    """Build a minimal parsed-entry dict, overriding any field."""

    entry = {
        "id": str(uuid.uuid4()),
        "title": "Title",
        "tags": [],
        "type": "idea",
        "resource": "",
        "part_of": [],
        "superseded_by": "",
        "content": "**Candidate:** something.\n**Status:** not_decided",
    }
    entry.update(overrides)

    # Ready
    return entry


@pytest.fixture()
def schema() -> Schema:
    """The packaged schema."""

    return load_schema(None)


# ===========================================================================
# check_entry — shared with remember
# ===========================================================================


class TestCheckEntry:
    """Per-entry rules, used both by doctor and by remember's warnings."""

    def test_conforming_entry_is_clean(self, schema: Schema) -> None:
        """A hub with its required field and body labels passes."""

        entry = _entry(
            type="hub",
            resource="/srv/project",
            content="**What it does:** things.\n**Stack:** Python.",
        )

        assert check_entry(entry, schema) == []

    def test_missing_required_field(self, schema: Schema) -> None:
        """A hub without `resource` is flagged."""

        entry = _entry(
            type="hub",
            content="**What it does:** things.\n**Stack:** Python.",
        )

        warnings = check_entry(entry, schema)

        assert any("resource" in warning for warning in warnings)

    def test_missing_body_field(self, schema: Schema) -> None:
        """A decision without its template fields is flagged."""

        entry = _entry(type="decision", content="Just prose.")

        warnings = check_entry(entry, schema)

        assert any("**Context:**" in warning for warning in warnings)

    def test_body_field_matches_inline_value_form(self, schema: Schema) -> None:
        """`**Decision: variant**` counts as the Decision field."""

        entry = _entry(
            type="feature",
            part_of=[str(uuid.uuid4())],
            content="**Decision: chosen variant** — why.\n**Implementation:** code.",
        )

        assert check_entry(entry, schema) == []

    def test_membership_required_without_part_of_warns(
        self, schema: Schema
    ) -> None:
        """A membership-required type without part_of gets a warning."""

        entry = _entry(
            type="feature",
            content="**Decision: variant** — why.\n**Implementation:** code.",
        )

        warnings = check_entry(entry, schema)

        assert any("part_of" in warning for warning in warnings)

    def test_unknown_type_has_no_rules(self, schema: Schema) -> None:
        """An undeclared type produces no per-field warnings here."""

        entry = _entry(type="mystery", content="Anything.")

        assert check_entry(entry, schema) == []


# ===========================================================================
# run_doctor — every defect kind
# ===========================================================================


class TestRunDoctor:
    """The full integrity pass."""

    def test_clean_kb(self, schema: Schema) -> None:
        """A conforming knowledge base reports nothing."""

        report = run_doctor([_entry(), _entry()], schema)

        assert report["entries_scanned"] == 2
        assert all(check["count"] == 0 for check in report["checks"].values())

    def test_dangling_link(self, schema: Schema) -> None:
        """A kb:// link to a missing entry is reported on the source."""

        source = _entry(content=f"See [x](kb://{uuid.uuid4()}#idea).")

        report = run_doctor([source], schema)

        assert report["checks"]["dangling_link"]["ids"] == [source["id"]]

    def test_link_to_superseded(self, schema: Schema) -> None:
        """Linking to a versioned-out entry is reported."""

        old = _entry(superseded_by=str(uuid.uuid4()))
        source = _entry(content=f"See [old](kb://{old['id']}#idea).")

        report = run_doctor([old, source], schema)

        assert report["checks"]["link_to_superseded"]["ids"] == [source["id"]]

    def test_type_outside_schema(self, schema: Schema) -> None:
        """Hand-edited files carrying an undeclared type are reported."""

        rogue = _entry(type="mystery")

        report = run_doctor([rogue], schema)

        assert report["checks"]["type_outside_schema"]["ids"] == [rogue["id"]]

    def test_missing_type_counts_as_outside_schema(self, schema: Schema) -> None:
        """An empty type is not in the schema either."""

        legacy = _entry(type="")

        report = run_doctor([legacy], schema)

        assert report["checks"]["type_outside_schema"]["count"] == 1

    def test_missing_required_and_body_fields(self, schema: Schema) -> None:
        """Both per-entry rule kinds land in their own buckets."""

        hub = _entry(type="hub", content="Nothing template-shaped here.")

        report = run_doctor([hub], schema)

        assert report["checks"]["missing_required_field"]["count"] == 1
        assert report["checks"]["missing_body_field"]["count"] == 1

    def test_tag_type_collision(self, schema: Schema) -> None:
        """A tag sharing a name with a type is reported."""

        entry = _entry(tags=["diagnostic", "engram_memory"])

        report = run_doctor([entry], schema)

        assert report["checks"]["tag_type_collision"]["ids"] == [entry["id"]]

    def test_high_degree(self) -> None:
        """A node above max_degree is reported as a supernode."""

        small_schema = parse_schema(
            {"version": 1, "limits": {"max_degree": 2}, "types": {"idea": {}}}
        )
        hub = _entry()
        sources = [
            _entry(content=f"[hub](kb://{hub['id']}#idea)") for _ in range(3)
        ]

        report = run_doctor([hub, *sources], small_schema)

        assert report["checks"]["high_degree"]["ids"] == [hub["id"]]
        assert report["max_degree"] == 2

    def test_digest_types_are_exempt_from_high_degree(self) -> None:
        """A type that summarizes its back-links is meant to accumulate them.

        Both nodes sit at the same degree; only the non-digesting one is
        a defect.
        """

        small_schema = parse_schema(
            {
                "version": 1,
                "limits": {"max_degree": 2},
                "types": {
                    "hub": {"digest_on_recall": True},
                    "idea": {},
                },
            }
        )
        hub = _entry(type="hub")
        plain = _entry(type="idea")
        sources = [
            _entry(
                content=(
                    f"[hub](kb://{hub['id']}#hub) "
                    f"[plain](kb://{plain['id']}#idea)"
                )
            )
            for _ in range(3)
        ]

        report = run_doctor([hub, plain, *sources], small_schema)

        assert report["checks"]["high_degree"]["ids"] == [plain["id"]]

    def test_missing_part_of(self, schema: Schema) -> None:
        """A membership-required type without part_of is reported."""

        bare = _entry(
            type="decision",
            content="**Context:** x.\n**Chosen:** y.",
        )

        report = run_doctor([bare], schema)

        assert report["checks"]["missing_part_of"]["ids"] == [bare["id"]]

    def test_part_of_dangling(self, schema: Schema) -> None:
        """A part_of target that does not exist is reported."""

        member = _entry(part_of=[str(uuid.uuid4())])

        report = run_doctor([member], schema)

        assert report["checks"]["part_of_dangling"]["ids"] == [member["id"]]

    def test_part_of_target_not_membership(self, schema: Schema) -> None:
        """part_of pointing at a non-hub (or superseded hub) is reported."""

        not_a_hub = _entry()
        old_hub = _entry(
            type="hub",
            resource="/srv/x",
            superseded_by=str(uuid.uuid4()),
            content="**What it does:** x.\n**Stack:** y.",
        )
        via_idea = _entry(part_of=[not_a_hub["id"]])
        via_old_hub = _entry(part_of=[old_hub["id"]])

        report = run_doctor([not_a_hub, old_hub, via_idea, via_old_hub], schema)

        assert sorted(
            report["checks"]["part_of_target_not_membership"]["ids"]
        ) == sorted([via_idea["id"], via_old_hub["id"]])

    def test_part_of_on_none_type(self, schema: Schema) -> None:
        """A membership-none type carrying part_of is reported."""

        hub = _entry(
            type="hub",
            resource="/srv/x",
            content="**What it does:** x.\n**Stack:** y.",
        )
        rogue_hub = _entry(
            type="hub",
            resource="/srv/y",
            part_of=[hub["id"]],
            content="**What it does:** y.\n**Stack:** z.",
        )

        report = run_doctor([hub, rogue_hub], schema)

        assert report["checks"]["part_of_on_none_type"]["ids"] == [rogue_hub["id"]]

    def test_part_of_does_not_count_into_degree(self) -> None:
        """Membership is not a graph edge — no high_degree from part_of."""

        small_schema = parse_schema(
            {
                "version": 1,
                "limits": {"max_degree": 2},
                "types": {
                    "hub": {"membership_target": True},
                    "idea": {},
                },
            }
        )
        hub = _entry(type="hub")
        members = [_entry(part_of=[hub["id"]]) for _ in range(3)]

        report = run_doctor([hub, *members], small_schema)

        assert report["checks"]["high_degree"]["count"] == 0

    def test_ids_are_capped(self, schema: Schema) -> None:
        """The report stays bounded on a large defect set."""

        rogues = [_entry(type="mystery") for _ in range(MAX_REPORTED_IDS + 5)]

        report = run_doctor(rogues, schema)
        check = report["checks"]["type_outside_schema"]

        assert check["count"] == MAX_REPORTED_IDS + 5
        assert len(check["ids"]) == MAX_REPORTED_IDS
        assert check["truncated"] is True


# ===========================================================================
# cleanup_candidate — usage-based, opt-in (needs an index usage snapshot)
# ===========================================================================


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class TestCleanupCandidate:
    """Entries flagged for review after a long read-free stretch.

    Ranking boost from access_count was removed after an eval A/B showed
    it net-harmful to search quality (see the engram idea entry); usage
    counters are kept only as this opt-in, non-blocking cleanup signal.
    """

    def test_skipped_without_usage_snapshot(self, schema: Schema) -> None:
        """No `usage` arg means the check never fires — old but unindexed
        entries aren't guessed at."""

        old = _entry(valid_at=_iso(1000))

        report = run_doctor([old], schema)

        assert report["checks"]["cleanup_candidate"]["count"] == 0

    def test_never_accessed_old_entry_is_flagged(self, schema: Schema) -> None:
        """Never read, created long ago — judged from valid_at."""

        old = _entry(valid_at=_iso(200))

        report = run_doctor([old], schema, usage={})

        assert report["checks"]["cleanup_candidate"]["ids"] == [old["id"]]

    def test_never_accessed_recent_entry_is_not_flagged(self, schema: Schema) -> None:
        """Newly created entries aren't cleanup candidates yet."""

        fresh = _entry(valid_at=_iso(5))

        report = run_doctor([fresh], schema, usage={})

        assert report["checks"]["cleanup_candidate"]["count"] == 0

    def test_recently_accessed_entry_is_not_flagged(self, schema: Schema) -> None:
        """A read history overrides an old valid_at — judged from
        last_accessed instead."""

        entry = _entry(valid_at=_iso(1000))
        usage = {entry["id"]: {"access_count": 3, "last_accessed": _iso(2)}}

        report = run_doctor([entry], schema, usage=usage)

        assert report["checks"]["cleanup_candidate"]["count"] == 0

    def test_accessed_long_ago_is_flagged(self, schema: Schema) -> None:
        """Read history exists but has itself gone stale."""

        entry = _entry(valid_at=_iso(5))
        usage = {entry["id"]: {"access_count": 1, "last_accessed": _iso(200)}}

        report = run_doctor([entry], schema, usage=usage)

        assert report["checks"]["cleanup_candidate"]["ids"] == [entry["id"]]

    def test_superseded_entry_is_never_flagged(self, schema: Schema) -> None:
        """An old version is already retired, not a cleanup target."""

        old_version = _entry(
            valid_at=_iso(1000), superseded_by=str(uuid.uuid4())
        )

        report = run_doctor([old_version], schema, usage={})

        assert report["checks"]["cleanup_candidate"]["count"] == 0

    def test_no_usage_signal_type_is_never_flagged(self) -> None:
        """Types exempt from the usage signal (e.g. hubs, read every
        session) are exempt from the cleanup check too."""

        exempt_schema = parse_schema(
            {
                "version": 1,
                "types": {"hub": {"usage_boost": False}},
            }
        )
        hub = _entry(type="hub", valid_at=_iso(1000))

        report = run_doctor([hub], exempt_schema, usage={})

        assert report["checks"]["cleanup_candidate"]["count"] == 0

    def test_no_anchor_is_never_flagged(self, schema: Schema) -> None:
        """Neither a read history nor a valid_at means there's nothing
        to judge age from — silence isn't evidence of staleness."""

        entry = _entry(valid_at="")

        report = run_doctor([entry], schema, usage={})

        assert report["checks"]["cleanup_candidate"]["count"] == 0

    def test_custom_threshold(self, schema: Schema) -> None:
        """stale_after_days is configurable."""

        entry = _entry(valid_at=_iso(10))

        report = run_doctor([entry], schema, usage={}, stale_after_days=5)

        assert report["checks"]["cleanup_candidate"]["ids"] == [entry["id"]]
