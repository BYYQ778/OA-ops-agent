from utils.kg_store import KGStore


def test_entities_are_deduplicated_and_persisted(tmp_path) -> None:
    store = KGStore(str(tmp_path))
    entity = {"entity_type": "SERVICE", "entity_name": "OA", "description": "OA 服务"}
    store.add_entities([entity], source_doc="runbook-a")
    store.add_entities([entity], source_doc="runbook-b")
    store.save()

    restored = KGStore(str(tmp_path))
    nodes = restored.search_nodes("OA")

    assert len(nodes) == 1
    assert nodes[0]["frequency"] == 2
    assert nodes[0]["sources"] == ["runbook-a", "runbook-b"]


def test_shortest_path_follows_persisted_cooccurrence_edges(tmp_path) -> None:
    store = KGStore(str(tmp_path))
    store.add_entities(
        [
            {"entity_type": "SERVICE", "entity_name": "OA"},
            {"entity_type": "DATABASE", "entity_name": "MySQL"},
        ],
        source_doc="architecture",
    )
    store.add_entities(
        [
            {"entity_type": "DATABASE", "entity_name": "MySQL"},
            {"entity_type": "HOST", "entity_name": "db01"},
        ],
        source_doc="deployment",
    )
    store.save()

    path = KGStore(str(tmp_path)).find_shortest_path("OA", "db01")

    assert path["path"] == ["OA", "MySQL", "db01"]
    assert path["length"] == 2
