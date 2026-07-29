"""
Tests for the dashboard FastAPI app.

Exercises the graph and CRUD endpoints against an isolated tmp_path
KnowledgeBase, mirroring test_database.py's fixture pattern.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dashboard.app import create_app
from database import KnowledgeBase

# Well-formed hub UUID for membership-required creates
_HUB_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture()
def kb(tmp_path: Path) -> KnowledgeBase:
    return KnowledgeBase(str(tmp_path))


@pytest.fixture()
def client(kb: KnowledgeBase) -> TestClient:
    return TestClient(create_app(kb))


def test_graph_reflects_relations(kb: KnowledgeBase, client: TestClient) -> None:
    a = kb.remember("Hub", "Hub content.", ["proj"], "hub")
    b = kb.remember(
        "Detail",
        f"See [hub](kb://{a['id']}#hub).",
        ["proj"],
        "feature",
        part_of=[a["id"]],
    )

    graph = client.get("/api/graph").json()

    node_ids = {n["id"] for n in graph["nodes"]}
    assert a["id"] in node_ids
    assert b["id"] in node_ids
    assert {
        "source_id": b["id"],
        "target_id": a["id"],
        "type": "hub",
        "edge": "related_to",
    } in graph["edges"]


def test_graph_includes_part_of_without_kb_link(
    kb: KnowledgeBase, client: TestClient
) -> None:
    a = kb.remember("Hub", "Hub content.", ["proj"], "hub")
    b = kb.remember(
        "Detail",
        "No back-link in the body — membership lives only in frontmatter.",
        ["proj"],
        "feature",
        part_of=[a["id"]],
    )

    graph = client.get("/api/graph").json()

    assert {
        "source_id": b["id"],
        "target_id": a["id"],
        "type": "part_of",
        "edge": "part_of",
    } in graph["edges"]


def test_create_via_post(client: TestClient) -> None:
    res = client.post(
        "/api/entries",
        json={
            "title": "New via API",
            "content": "Body.",
            "tags": ["proj"],
            "entry_type": "snippet",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "created"

    fetched = client.get(f"/api/entries/{body['id']}").json()
    assert fetched["title"] == "New via API"


def test_update_round_trips(kb: KnowledgeBase, client: TestClient) -> None:
    created = kb.remember("Original", "Body.", ["proj"], "snippet")

    res = client.patch(
        f"/api/entries/{created['id']}",
        json={
            "title": "Updated",
            "content": "New body.",
            "tags": ["proj"],
            "entry_type": "snippet",
        },
    )
    assert res.status_code == 200
    assert res.json()["action"] == "updated"

    fetched = client.get(f"/api/entries/{created['id']}").json()
    assert fetched["title"] == "Updated"
    assert fetched["content"] == "New body."


def test_supersede_creates_new_version(kb: KnowledgeBase, client: TestClient) -> None:
    created = kb.remember(
        "Original", "Body.", ["proj"], "decision", part_of=[_HUB_ID]
    )

    res = client.patch(
        f"/api/entries/{created['id']}",
        json={
            "title": "Original",
            "content": "Reversed decision.",
            "tags": ["proj"],
            "entry_type": "decision",
            "supersede": True,
        },
    )
    assert res.status_code == 200
    assert res.json()["action"] == "superseded"

    old = client.get(f"/api/entries/{created['id']}").json()
    assert old["superseded_by"]


def test_delete_removes_entry(kb: KnowledgeBase, client: TestClient) -> None:
    created = kb.remember("Temp", "Body.", ["proj"], "snippet")

    res = client.delete(f"/api/entries/{created['id']}")
    assert res.status_code == 200

    assert client.get(f"/api/entries/{created['id']}").status_code == 404


def test_delete_missing_returns_404(client: TestClient) -> None:
    res = client.delete("/api/entries/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404


def test_create_rejects_type_outside_schema(client: TestClient) -> None:
    res = client.post(
        "/api/entries",
        json={
            "title": "Bogus type",
            "content": "Body.",
            "tags": ["proj"],
            "entry_type": "musing",
        },
    )

    assert res.status_code == 400
    assert "musing" in res.json()["detail"]


def test_update_rejects_type_outside_schema(
    kb: KnowledgeBase, client: TestClient
) -> None:
    created = kb.remember("Original", "Body.", ["proj"], "snippet")

    res = client.patch(
        f"/api/entries/{created['id']}",
        json={
            "title": "Original",
            "content": "Body.",
            "tags": ["proj"],
            "entry_type": "musing",
        },
    )

    assert res.status_code == 400
    assert kb.get(created["id"])["type"] == "snippet"
