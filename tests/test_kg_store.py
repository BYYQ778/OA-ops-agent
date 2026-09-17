import json

from utils.kg_store import KGStore, _node_id


def _require_node(store, node_id: str):
    """get_node 返回 Optional；先断言存在再取用。"""
    node = store.get_node(node_id)
    assert node is not None
    return node


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


# ========== 追加：JSONL 加载、文档清理、子图与上下文渲染 ==========


def test_load_skips_blank_and_malformed_lines(tmp_path) -> None:
    (tmp_path / "nodes.jsonl").write_text(
        "\n"
        + json.dumps({"id": "n1", "type": "SERVICE", "name": "OA", "description": "入口服务"}) + "\n"
        + "{不是合法 JSON\n"
        + json.dumps({"id": "n2", "name": "MySQL"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "edges.jsonl").write_text(
        "\n"
        + json.dumps({"source_id": "n2", "target_id": "n1", "relation": "depends_on", "weight": 3}) + "\n"
        + "破坏行\n"
        + json.dumps({"source_id": "n1", "target_id": "missing-node", "weight": 9}) + "\n",
        encoding="utf-8",
    )

    store = KGStore(str(tmp_path))

    assert store.graph.number_of_nodes() == 2
    assert store.graph.number_of_edges() == 1
    assert store.graph["n2"]["n1"]["weight"] == 3
    assert store.graph["n2"]["n1"]["relation"] == "depends_on"
    assert _require_node(store, "n1")["sources"] == []
    assert _require_node(store, "n2")["type"] == "CONCEPT"


def test_load_without_edges_file(tmp_path) -> None:
    (tmp_path / "nodes.jsonl").write_text(
        json.dumps({"id": "n1", "name": "OA"}) + "\n", encoding="utf-8"
    )

    store = KGStore(str(tmp_path))

    assert store.graph.number_of_nodes() == 1
    assert store.graph.number_of_edges() == 0


def test_add_entities_skips_blank_names_and_tracks_sources(tmp_path) -> None:
    store = KGStore(str(tmp_path))
    store.add_entities(
        [
            {"entity_type": "SERVICE", "entity_name": "OA", "description": ""},
            {"entity_type": "SERVICE", "entity_name": "   "},
        ],
        source_doc="runbook",
    )
    store.add_entities([{"entity_type": "SERVICE", "entity_name": "OA"}])
    store.add_entities([{"entity_type": "SERVICE", "entity_name": "OA"}], source_doc="runbook")

    node = store.get_node(_node_id("SERVICE", "OA"))

    assert store.graph.number_of_nodes() == 1
    assert node is not None
    assert node["frequency"] == 3
    assert node["sources"] == ["runbook"]
    assert node["description"] == ""


def test_add_entities_merges_descriptions_and_reinforces_edges(tmp_path) -> None:
    store = KGStore(str(tmp_path))
    oa = {"entity_type": "SERVICE", "entity_name": "OA", "description": "统一认证入口"}
    mysql = {"entity_type": "DATABASE", "entity_name": "MySQL", "description": "用户库"}

    store.add_entities([oa, mysql], source_doc="arch")
    store.add_entities(
        [{"entity_type": "SERVICE", "entity_name": "OA", "description": "工单入口"}, mysql],
        source_doc="ops",
    )
    store.add_entities([{"entity_type": "TECHNOLOGY", "entity_name": "Nginx", "description": ""}])
    store.add_entities(
        [{"entity_type": "TECHNOLOGY", "entity_name": "Nginx", "description": "反向代理"}]
    )

    assert _require_node(store, _node_id("SERVICE", "OA"))["description"] == "统一认证入口; 工单入口"
    assert _require_node(store, _node_id("DATABASE", "MySQL"))["description"] == "用户库"
    assert _require_node(store, _node_id("TECHNOLOGY", "Nginx"))["description"] == "反向代理"

    edge = store.graph[_node_id("SERVICE", "OA")][_node_id("DATABASE", "MySQL")]
    assert edge["weight"] == 2
    assert edge["relation"] == "co_occurs"


def test_remove_document_prunes_unique_nodes_and_keeps_shared(tmp_path) -> None:
    store = KGStore(str(tmp_path))
    store.add_entities(
        [
            {"entity_type": "SERVICE", "entity_name": "OA"},
            {"entity_type": "DATABASE", "entity_name": "MySQL"},
        ],
        source_doc="runbook-a",
    )
    store.add_entities([{"entity_type": "SERVICE", "entity_name": "OA"}], source_doc="runbook-b")
    store.add_entities([{"entity_type": "TECHNOLOGY", "entity_name": "Nginx"}], source_doc="runbook-c")

    store.remove_document("runbook-a")

    assert store.search_nodes("MySQL") == []
    assert _require_node(store, _node_id("SERVICE", "OA"))["sources"] == ["runbook-b"]
    assert _require_node(store, _node_id("TECHNOLOGY", "Nginx"))["sources"] == ["runbook-c"]
    assert store.graph.number_of_edges() == 0


def test_clear_removes_graph_and_persisted_files(tmp_path) -> None:
    store = KGStore(str(tmp_path))
    store.add_entities([{"entity_type": "SERVICE", "entity_name": "OA"}], source_doc="runbook")
    store.save()
    assert (tmp_path / "nodes.jsonl").exists()

    store.clear()
    store.clear()

    assert store.graph.number_of_nodes() == 0
    assert not (tmp_path / "nodes.jsonl").exists()
    assert not (tmp_path / "edges.jsonl").exists()


def test_search_nodes_filters_by_entity_type_and_limit(tmp_path) -> None:
    store = KGStore(str(tmp_path))
    store.add_entities(
        [
            {"entity_type": "SERVICE", "entity_name": "OA 门户", "description": "统一入口"},
            {"entity_type": "DATABASE", "entity_name": "OA 数据库", "description": "用户数据"},
            {"entity_type": "DATABASE", "entity_name": "Redis", "description": "缓存"},
        ]
    )

    databases = store.search_nodes("oa", entity_type="DATABASE")

    assert [node["name"] for node in databases] == ["OA 数据库"]
    assert len(store.search_nodes("oa")) == 2
    assert len(store.search_nodes("OA", limit=1)) == 1
    assert store.search_nodes("不存在的实体") == []


def test_get_node_and_neighbors_handle_unknown_ids(tmp_path) -> None:
    store = KGStore(str(tmp_path))

    assert store.get_node("missing-node") is None
    assert store.get_neighbors("missing-node") == []
    assert store.extract_subgraph("missing-node", depth=2) == {"nodes": [], "edges": []}


def test_get_neighbors_sorted_by_relation_weight(tmp_path) -> None:
    store = KGStore(str(tmp_path))
    oa = {"entity_type": "SERVICE", "entity_name": "OA"}
    mysql = {"entity_type": "DATABASE", "entity_name": "MySQL"}
    redis = {"entity_type": "TECHNOLOGY", "entity_name": "Redis"}
    store.add_entities([oa, mysql, redis], source_doc="arch")
    store.add_entities([oa, mysql], source_doc="ops")

    neighbors = store.get_neighbors(_node_id("SERVICE", "OA"))

    assert [node["name"] for node in neighbors] == ["MySQL", "Redis"]
    assert neighbors[0]["relation"] == "co_occurs"
    assert neighbors[0]["weight"] == 2
    assert neighbors[1]["weight"] == 1

    sub = store.extract_subgraph(_node_id("SERVICE", "OA"), depth=1)
    assert {node["name"] for node in sub["nodes"]} == {"OA", "MySQL", "Redis"}


def test_extract_subgraph_respects_depth(tmp_path) -> None:
    store = KGStore(str(tmp_path))
    entities = [
        {"entity_type": "SERVICE", "entity_name": "OA"},
        {"entity_type": "DATABASE", "entity_name": "MySQL"},
        {"entity_type": "HOST", "entity_name": "db01"},
        {"entity_type": "HOST", "entity_name": "db02"},
    ]
    store.add_entities(entities[:2], source_doc="a")
    store.add_entities(entities[1:3], source_doc="b")
    store.add_entities(entities[2:], source_doc="c")

    mysql_id = _node_id("DATABASE", "MySQL")
    shallow = store.extract_subgraph(mysql_id, depth=1)
    deep = store.extract_subgraph(mysql_id, depth=3)

    assert {node["name"] for node in shallow["nodes"]} == {"MySQL", "db01"}
    assert [(edge["source_id"], edge["target_id"]) for edge in shallow["edges"]] == [
        (mysql_id, _node_id("HOST", "db01"))
    ]
    assert shallow["edges"][0]["relation"] == "co_occurs"
    assert {node["name"] for node in deep["nodes"]} == {"MySQL", "db01", "db02"}


def test_find_shortest_path_reports_missing_and_disconnected_entities(tmp_path) -> None:
    store = KGStore(str(tmp_path))
    store.add_entities(
        [
            {"entity_type": "SERVICE", "entity_name": "OA"},
            {"entity_type": "DATABASE", "entity_name": "MySQL"},
        ],
        source_doc="a",
    )
    store.add_entities(
        [
            {"entity_type": "HOST", "entity_name": "isolated-01"},
            {"entity_type": "HOST", "entity_name": "isolated-02"},
        ],
        source_doc="b",
    )

    missing = store.find_shortest_path("OA", "不存在的系统")
    disconnected = store.find_shortest_path("OA", "isolated-01")

    assert missing == {"path": [], "length": 0, "error": "未找到匹配的实体"}
    assert disconnected["path"] == []
    assert disconnected["length"] == -1
    assert "无路径" in disconnected["error"]


def test_get_stats_and_context_string(tmp_path) -> None:
    store = KGStore(str(tmp_path))

    assert store.to_context_string() == "知识图谱当前为空。"
    assert store.get_stats() == {
        "total_nodes": 0,
        "total_edges": 0,
        "nodes_by_type": {},
        "storage_dir": str(tmp_path),
    }

    oa = {"entity_type": "SERVICE", "entity_name": "OA", "description": "统一认证入口，支撑单点登录"}
    mysql = {"entity_type": "DATABASE", "entity_name": "MySQL", "description": "用户数据库"}
    store.add_entities([oa, mysql], source_doc="运维手册")
    store.add_entities([oa], source_doc="巡检记录")

    context = store.to_context_string("OA")
    assert "知识图谱概况: 2 节点, 1 边" in context
    assert "'SERVICE': 1" in context
    assert "[SERVICE] OA — 统一认证入口" in context
    assert "(来源: 运维手册, 巡检记录)" in context
    assert "MySQL" not in context

    ranked = store.to_context_string()
    assert ranked.index("OA") < ranked.index("MySQL")
    assert store.get_stats()["nodes_by_type"] == {"SERVICE": 1, "DATABASE": 1}


def test_context_string_previews_only_first_sources(tmp_path) -> None:
    store = KGStore(str(tmp_path))
    entity = {"entity_type": "SERVICE", "entity_name": "OA"}
    for doc in ["手册一", "手册二", "手册三"]:
        store.add_entities([entity], source_doc=doc)

    context = store.to_context_string()

    assert "手册一" in context
    assert "(来源: 手册一, 手册二)" in context
    assert "手册三" not in context
