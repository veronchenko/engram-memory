"""
Knowledge base integrity checks, driven by the entry schema.

One validation pass over the Markdown entries — the source of truth, not
the SQLite cache — reporting the defect classes that accumulate when a
convention has no validator behind it: dangling and superseded link
targets, types outside the schema, missing required frontmatter/body
fields, supernodes, and tag/type namespace collisions.

Exposed as the `doctor` MCP tool and reused by `rebuild` for its
non-blocking warnings. `check_entry` is shared with `remember`, so a
per-entry rule has exactly one implementation.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Final

from schema import Schema, TypeRule
from search_backend import extract_relations

logger = logging.getLogger("engram")

# Ids reported per defect kind — the response stays bounded on a KB of
# any size; `count` always carries the true total.
MAX_REPORTED_IDS: Final[int] = 50

# An entry with no reads for this long is flagged as a cleanup candidate —
# not deleted, just surfaced for a human to review (retroactive "brainrot
# cleanup", TODO #3's original framing, split from ranking after an eval
# A/B showed folding the same signal into search score was net-harmful).
DEFAULT_STALE_AFTER_DAYS: Final[float] = 90.0

# Every defect kind, so the response shape is stable even when clean
CHECK_KINDS: Final[tuple[str, ...]] = (
    "dangling_link",
    "link_to_superseded",
    "type_outside_schema",
    "missing_required_field",
    "missing_body_field",
    "high_degree",
    "tag_type_collision",
    "missing_part_of",
    "part_of_dangling",
    "part_of_target_not_membership",
    "part_of_on_none_type",
    "cleanup_candidate",
)


def _body_field_pattern(label: str) -> re.Pattern[str]:
    """
    Build the regex matching a template's bold body field.

    Matches both `**Label:** value` and `**Label: value**` — the two
    forms the templates use — anchored to the start of a line.

    Args:
        label: Field label from the schema's `body` list.

    Returns:
        Compiled case-insensitive multiline pattern.
    """

    # Anchored at line start, colon required after the label
    return re.compile(
        rf"^\*\*\s*{re.escape(label)}\s*:", re.MULTILINE | re.IGNORECASE
    )


def missing_required_fields(entry: dict[str, Any], rule: TypeRule) -> list[str]:
    """
    Frontmatter fields the type requires but the entry leaves empty.

    Args:
        entry: Parsed entry dict.
        rule: Rules for the entry's type.

    Returns:
        Names of the missing fields, in schema order.
    """

    # Empty string and absent are equivalent here
    return [
        field_name
        for field_name in rule.required
        if not str(entry.get(field_name, "")).strip()
    ]


def missing_body_fields(entry: dict[str, Any], rule: TypeRule) -> list[str]:
    """
    Template body labels the type requires but the entry's body lacks.

    Args:
        entry: Parsed entry dict.
        rule: Rules for the entry's type.

    Returns:
        Missing labels, in schema order.
    """

    content = entry.get("content", "")
    # Labels with no matching bold header line
    return [
        label for label in rule.body if not _body_field_pattern(label).search(content)
    ]


def _days_since(iso_ts: str, now: datetime) -> float | None:
    """
    Age in days of an ISO8601 timestamp, or None if unparseable/empty.

    Args:
        iso_ts: Timestamp string (naive values are treated as UTC).
        now: Reference time to measure age against.

    Returns:
        Age in days, or None if `iso_ts` is empty or malformed.
    """

    if not iso_ts:
        return None
    try:
        at = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return max((now - at).total_seconds(), 0.0) / 86400


def is_cleanup_candidate(
    entry: dict[str, Any],
    usage_row: dict[str, Any] | None,
    stale_after_days: float,
    now: datetime,
) -> bool:
    """
    Whether an entry has gone long enough without a read to flag for review.

    Never-accessed entries are judged from `valid_at` (age since creation/
    last edit); once-accessed entries are judged from `last_accessed`. An
    entry with neither (no usage row and no valid_at) can't be judged and
    is never flagged — silence isn't evidence of staleness.

    Args:
        entry: Parsed entry dict.
        usage_row: This entry's {access_count, last_accessed} from the
            index, or None if it isn't indexed yet.
        stale_after_days: Age threshold in days.
        now: Reference time.

    Returns:
        True if the entry should be flagged as a cleanup candidate.
    """

    access_count = usage_row.get("access_count", 0) if usage_row else 0
    last_accessed = usage_row.get("last_accessed", "") if usage_row else ""

    anchor = last_accessed if access_count > 0 else str(entry.get("valid_at", ""))
    age_days = _days_since(anchor, now)
    return age_days is not None and age_days > stale_after_days


def check_entry(entry: dict[str, Any], schema: Schema) -> list[str]:
    """
    Validate one entry against its type's schema rules.

    Args:
        entry: Parsed entry dict (id, title, tags, type, resource, content).
        schema: The loaded validation contract.

    Returns:
        Human-readable warnings, empty when the entry conforms. An
        unknown type yields no warnings here — it has no rules to check
        against, and is reported separately by run_doctor.
    """

    rule = schema.rule(str(entry.get("type", "")))
    if rule is None:
        # No rules declared for this type
        return []

    warnings = [
        f"Missing required field '{field_name}' for type '{entry['type']}'."
        for field_name in missing_required_fields(entry, rule)
    ]

    # Membership is enforced hard only on create — here it is a warning,
    # so pre-part_of entries stay updatable until the migration lands.
    if rule.membership == "required" and not entry.get("part_of"):
        warnings.append(
            f"Type '{entry['type']}' requires part_of — set the UUID(s) "
            "of the hub entry this belongs to."
        )

    missing_body = missing_body_fields(entry, rule)
    if missing_body:
        warnings.append(
            f"Body is missing the '{entry['type']}' template field(s): "
            + ", ".join(f"**{label}:**" for label in missing_body)
            + "."
        )

    return warnings


def run_doctor(
    entries: Iterable[dict[str, Any]],
    schema: Schema,
    usage: dict[str, dict[str, Any]] | None = None,
    stale_after_days: float = DEFAULT_STALE_AFTER_DAYS,
) -> dict[str, Any]:
    """
    Run every integrity check over the knowledge base.

    Relations are recomputed from the entries' Markdown content rather
    than read from the search index, so the report describes the source
    of truth even when the index is stale. `usage` is the one exception —
    access_count/last_accessed live only in the index (see CLAUDE.md), so
    a caller that wants the cleanup-candidate check must supply a snapshot
    of it (e.g. SQLiteBackend.get_usage_snapshot()); without it, that one
    check is skipped and everything else is unaffected.

    Types flagged `digest_on_recall` are exempt from the supernode check:
    accumulating back-links is what they are for, which is why `recall`
    summarizes their incoming links instead of listing them. Reporting
    that as a defect would be permanently red and would bury the entries
    whose degree grew by accident.

    Args:
        entries: Parsed entries (as produced by KnowledgeBase.iter_entries).
        schema: The loaded validation contract.
        usage: Optional entry id -> {access_count, last_accessed} snapshot
            from the index, for the cleanup-candidate check.
        stale_after_days: Days with no read (or since valid_at, if never
            read) before an entry is flagged as a cleanup candidate.

    Returns:
        Dict with 'entries_scanned', 'max_degree' (the threshold used),
        and 'checks' — a mapping of defect kind to {count, ids,
        truncated}, where count is the number of affected entries and
        ids names them (capped at MAX_REPORTED_IDS).
    """

    entry_list = list(entries)
    by_id = {entry["id"]: entry for entry in entry_list}
    type_names = set(schema.types)
    digest_types = schema.types_where("digest_on_recall", True)
    membership_targets = schema.types_where("membership_target", True)
    no_usage_signal_types = schema.types_where("usage_boost", False)

    found: dict[str, set[str]] = {kind: set() for kind in CHECK_KINDS}
    degree: defaultdict[str, int] = defaultdict(int)
    now = datetime.now(timezone.utc)

    for entry in entry_list:
        entry_id = entry["id"]
        entry_type = str(entry.get("type", ""))
        rule = schema.rule(entry_type)

        if rule is None:
            found["type_outside_schema"].add(entry_id)
        else:
            if missing_required_fields(entry, rule):
                found["missing_required_field"].add(entry_id)
            if missing_body_fields(entry, rule):
                found["missing_body_field"].add(entry_id)

        if any(tag in type_names for tag in entry.get("tags", [])):
            found["tag_type_collision"].add(entry_id)

        # Cleanup-candidate check: opt-in (needs an index-sourced usage
        # snapshot), skipped for types exempt from usage signals (hubs are
        # read every session, not a meaningful staleness indicator) and
        # for superseded entries (already retired, not a cleanup target).
        if (
            usage is not None
            and entry_type not in no_usage_signal_types
            and not entry.get("superseded_by", "")
            and is_cleanup_candidate(entry, usage.get(entry_id), stale_after_days, now)
        ):
            found["cleanup_candidate"].add(entry_id)

        # Membership checks. part_of is deliberately NOT counted into
        # `degree` — membership stopped being a graph edge when it moved
        # out of the body into frontmatter.
        part_of = entry.get("part_of", [])
        if rule is not None:
            if rule.membership == "required" and not part_of:
                found["missing_part_of"].add(entry_id)
            if rule.membership == "none" and part_of:
                found["part_of_on_none_type"].add(entry_id)
        for target_id in part_of:
            target = by_id.get(target_id)
            if target is None:
                found["part_of_dangling"].add(entry_id)
            elif (
                str(target.get("type", "")) not in membership_targets
                or target.get("superseded_by", "")
            ):
                # Wrong-typed target, or a hub replaced by a newer version
                found["part_of_target_not_membership"].add(entry_id)

        for relation in extract_relations(
            entry.get("content", ""), schema.edge_types
        ):
            target_id = relation["target"]
            degree[entry_id] += 1

            target = by_id.get(target_id)
            if target is None:
                found["dangling_link"].add(entry_id)
                continue

            degree[target_id] += 1
            if target.get("superseded_by", ""):
                found["link_to_superseded"].add(entry_id)

    found["high_degree"] = {
        entry_id
        for entry_id, count in degree.items()
        if count > schema.max_degree
        and entry_id in by_id
        and str(by_id[entry_id].get("type", "")) not in digest_types
    }

    checks = {
        kind: {
            "count": len(ids),
            "ids": sorted(ids)[:MAX_REPORTED_IDS],
            "truncated": len(ids) > MAX_REPORTED_IDS,
        }
        for kind, ids in found.items()
    }

    total = sum(int(check["count"]) for check in checks.values())
    logger.info(
        "Doctor scanned %d entries, found %d affected entries", len(entry_list), total
    )

    return {
        "entries_scanned": len(entry_list),
        "max_degree": schema.max_degree,
        "checks": checks,
    }
