"""LLM 端到端子集评测（第 3 周）：真实 Agent（LangGraph + Ollama）跑 30 条子集。

用法（env_new 环境，工作树根目录）:
    env -u PYTHONPATH env_new/Scripts/python.exe scripts/run_llm_subset.py --check
    env -u PYTHONPATH env_new/Scripts/python.exe scripts/run_llm_subset.py --prepare
    env -u PYTHONPATH env_new/Scripts/python.exe scripts/run_llm_subset.py --limit 2   # 冒烟
    env -u PYTHONPATH env_new/Scripts/python.exe scripts/run_llm_subset.py             # 全量
    env -u PYTHONPATH env_new/Scripts/python.exe scripts/run_llm_subset.py --provider deepseek  # 云端（需授权，读 .env 的 OA_LLM_API_KEY）

产物: evals/results/llm-subset-<ts>.json（含 markdown 区块，供报告生成器消费）
      过程中逐条追加 evals/results/llm-subset-<ts>.jsonl（可断点续跑：--skip-done 读回已有 JSONL）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.dataset import QAItem, load_corpus, load_jsonl, validate  # noqa: E402
from evals.metrics import latency_stats  # noqa: E402
from evals.pipelines import HybridPipeline  # noqa: E402

SUBSET_PATH = PROJECT_ROOT / "evals" / "dataset" / "llm_subset_v1.jsonl"
DATASET_PATH = PROJECT_ROOT / "evals" / "dataset" / "qa_v1.jsonl"
CORPUS_DIR = PROJECT_ROOT / "evals" / "corpus"
INDEX_DIR = PROJECT_ROOT / ".eval-index-llm"
AGENT_COLLECTION = "oa_knowledge_base"  # 与生产 KnowledgeBaseAgent 一致
REFUSE_MARK = "抱歉，知识库中未找到相关信息"
CITE_RE = re.compile(r"《([^》]+)》")


def load_subset() -> List[Dict[str, Any]]:
    rows = load_jsonl(SUBSET_PATH)
    return [dict(data, _line=lineno) for lineno, data in rows]


def load_dataset_items() -> Dict[str, QAItem]:
    report = validate(load_jsonl(DATASET_PATH), load_corpus(CORPUS_DIR), strict_counts=True)
    if report.errors:
        raise ValueError("评测集校验未通过: " + "; ".join(report.errors[:3]))
    return {item.id: item for item in report.items}


def ollama_status() -> Dict[str, Any]:
    import urllib.request

    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"ready": True, "models": [m.get("name") for m in data.get("models", [])]}
    except Exception as exc:  # noqa: BLE001 - 需要把失败原因带回
        return {"ready": False, "error": f"{type(exc).__name__}: {exc}"}


def _read_env_key(name: str) -> str:
    """从项目 .env 读取密钥（不打印明文）；优先 worktree，其次主目录。"""
    candidates = [PROJECT_ROOT / ".env", PROJECT_ROOT.parent / "oa-ops-agent" / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def prepare_index() -> int:
    pipeline = HybridPipeline(INDEX_DIR / "hybrid", collection_name=AGENT_COLLECTION)
    return pipeline.build_index(CORPUS_DIR)


def build_agent(provider: str) -> Any:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import agents.knowledge_agent as ka

    ka.CHROMA_DB_DIR = str(INDEX_DIR / "hybrid")  # 重定向到评测索引（仅本进程）
    from utils.config import config as app_config

    if provider == "ollama":
        base_url = str(app_config.get("llm.ollama.base_url", "http://localhost:11434/v1"))
        model = str(app_config.get("llm.ollama.model", "qwen3:8b"))
        api_key = str(app_config.get("llm.ollama.api_key", "ollama") or "ollama")
    else:
        base_url = str(app_config.get("llm.base_url", "https://api.deepseek.com/v1"))
        model = str(app_config.get("llm.model", "deepseek-chat"))
        api_key = os.environ.get("OA_LLM_API_KEY") or _read_env_key("OA_LLM_API_KEY")
        if not api_key:
            raise SystemExit("云端模式需要 OA_LLM_API_KEY（.env）；未找到，已中止。")
    return ka.KnowledgeBaseAgent(llm_api_key=api_key, llm_base_url=base_url, llm_model=model)


def classify(answer: str, gold_docs: List[str], routes: List[str]) -> Dict[str, Any]:
    answer = answer or ""
    refused = REFUSE_MARK in answer
    citations = CITE_RE.findall(answer)
    grounded = any(any(g in c or c in g for g in gold_docs) for c in citations)
    return {
        "refused": refused,
        "citations": citations[:6],
        "grounded": grounded,
        "gate_refused": refused and not routes,
        "llm_refused": refused and bool(routes),
    }


def markdown_section(summary: Dict[str, Any], records: List[Dict[str, Any]], provider: str = "ollama") -> str:
    lines = [
        f"LLM 端到端子集（真实 Agent：LangGraph + {provider}；先检索门控，后回答级拒答契约）：\n",
        "| 指标 | 数值 |", "|---|---|",
        f"| 子集规模 | {summary['counts']['total']} 条（可回答 {summary['counts']['answerable']} / 无证据 {summary['counts']['unanswerable']}） |",
        f"| 系统级无证据拒答率 | {summary['system_refusal_rate_unanswerable']:.1%}（门控 {summary['gate_refusal_count_unanswerable']} + 回答级 {summary['llm_refusal_count_unanswerable']} / {summary['counts']['unanswerable']}） |",
        f"| 有依据回答率（引对 gold） | {summary['grounded_answer_rate']:.1%} |",
        f"| 可回答条目误拒（系统级） | {summary['false_refusal_system_rate']:.1%} |",
        f"| 工具选择准确率（首跳=检索） | {summary['tool_accuracy']:.1%}（{summary['tool_ok']}/{summary['tool_total']}，门控直拒不参与） |",
        f"| 平均推理步数 | {summary['avg_steps']:.1f} |",
        f"| 失败率 | {summary['failure_rate']:.1%} |",
        f"| 端到端延迟 mean / p95 | {summary['latency_ms']['mean']:.0f} ms / {summary['latency_ms']['p95']:.0f} ms |",
        "",
        "| qid | 结果 | 引用 | 路由 | 秒 |", "|---|---|---|---|---|",
    ]
    for row in records:
        result = "拒答" if row.get("refused") else ("有依据回答" if row.get("grounded") else "回答(引用不符)")
        cites = "、".join(row.get("citations", [])[:2]) or "-"
        lines.append(
            f"| {row['qid']} | {result} | {cites[:40]} | {'>'.join(row.get('routes') or ['-'])} | {row.get('latency_ms', 0) / 1000:.1f} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LLM 端到端子集评测")
    parser.add_argument("--check", action="store_true", help="仅检查 Ollama 与索引状态")
    parser.add_argument("--prepare", action="store_true", help="构建子集索引后退出")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条（冒烟）")
    parser.add_argument("--only", type=str, default=None, help="逗号分隔 qid 列表")
    parser.add_argument(
        "--provider",
        choices=["auto", "ollama", "deepseek"],
        default="auto",
        help="LLM 提供方（auto=按 config.yaml llm.provider）",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    subset = load_subset()
    dataset = load_dataset_items()
    missing = [row["qid"] for row in subset if row["qid"] not in dataset]
    if missing:
        print(f"子集引用了不存在的 qid: {missing}")
        return 2

    if args.prepare:
        count = prepare_index()
        print(f"子集索引已构建: {count} 块 → {INDEX_DIR / 'hybrid'}（collection={AGENT_COLLECTION}）")
        return 0

    provider = args.provider
    if provider == "auto":
        from utils.config import config as app_config

        provider = str(app_config.get("llm.provider", "ollama"))

    if provider == "ollama":
        status = ollama_status()
        if not status["ready"]:
            print(f"Ollama 未就绪: {status.get('error')}")
            print("请先启动 Ollama（例如运行 scripts/setup_ollama.bat 或 `ollama serve`），再重试。")
            return 3
    else:
        if not (os.environ.get("OA_LLM_API_KEY") or _read_env_key("OA_LLM_API_KEY")):
            print("云端模式缺少 OA_LLM_API_KEY（.env）；已中止。")
            return 4
        status = {"ready": True, "models": []}

    if not (INDEX_DIR / "hybrid").exists():
        count = prepare_index()
        print(f"子集索引已构建: {count} 块")

    if args.check:
        print(f"provider={provider}；" + (f"Ollama 模型列表: {status['models']}" if provider == "ollama" else "云端密钥已就绪"))
        print(f"索引 OK: {INDEX_DIR / 'hybrid'}")
        return 0

    selected = subset
    if args.only:
        wanted = {q.strip() for q in args.only.split(",")}
        selected = [row for row in subset if row["qid"] in wanted]
    if args.limit:
        selected = selected[: args.limit]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = args.out or (PROJECT_ROOT / "evals" / "results" / f"llm-subset-{stamp}.json")
    jsonl_path = out_path.with_suffix(".jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    agent = build_agent(provider)
    routes: List[str] = []
    original = agent._router_decide

    def spy(router_msg: str, has_kg: bool):
        action, tool_input = original(router_msg, has_kg)
        routes.append(action)
        return action, tool_input

    agent._router_decide = spy  # 记录每次路由决策（步骤数/首跳）

    records: List[Dict[str, Any]] = []
    for i, row in enumerate(selected, start=1):
        item = dataset[row["qid"]]
        routes.clear()
        started = time.perf_counter()
        failure: Optional[str] = None
        answer = ""
        try:
            answer = agent.query(item.question)
        except Exception as exc:  # noqa: BLE001 - 单条失败不应中断整批
            failure = f"{type(exc).__name__}: {exc}"[:300]
        latency_ms = (time.perf_counter() - started) * 1000.0
        record: Dict[str, Any] = {
            "qid": item.id,
            "question": item.question,
            "answerable": item.answerable,
            "expect_refusal": bool(row.get("expect_refusal")),
            "gold_docs": list(item.gold_docs),
            "answer_excerpt": (answer or "")[:220],
            "routes": list(routes),
            "latency_ms": round(latency_ms, 1),
            "failure": failure,
            **classify(answer, list(item.gold_docs), routes),
        }
        records.append(record)
        with jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(
            f"[{i}/{len(selected)}] {item.id} {'拒答' if record['refused'] else ('有据' if record['grounded'] else '回答')} "
            f"routes={record['routes']} {latency_ms / 1000:.1f}s {'FAIL ' + failure if failure else ''}",
            flush=True,
        )

    answerable = [r for r in records if r["answerable"]]
    negatives = [r for r in records if not r["answerable"]]
    reached_router = [r for r in records if r["routes"]]
    tool_ok = sum(1 for r in reached_router if r["routes"][0] == "search_kb")
    summary = {
        "counts": {
            "total": len(records),
            "answerable": len(answerable),
            "unanswerable": len(negatives),
        },
        "system_refusal_rate_unanswerable": (
            sum(1 for r in negatives if r["refused"]) / len(negatives) if negatives else 0.0
        ),
        "gate_refusal_count_unanswerable": sum(1 for r in negatives if r["gate_refused"]),
        "llm_refusal_count_unanswerable": sum(1 for r in negatives if r["llm_refused"]),
        "grounded_answer_rate": (
            sum(1 for r in answerable if r["grounded"]) / len(answerable) if answerable else 0.0
        ),
        "false_refusal_system_rate": (
            sum(1 for r in answerable if r["refused"]) / len(answerable) if answerable else 0.0
        ),
        "tool_accuracy": tool_ok / len(reached_router) if reached_router else 0.0,
        "tool_ok": tool_ok,
        "tool_total": len(reached_router),
        "avg_steps": (
            sum(len(r["routes"]) for r in reached_router) / len(reached_router) if reached_router else 0.0
        ),
        "failure_rate": sum(1 for r in records if r["failure"]) / len(records) if records else 0.0,
        "latency_ms": latency_stats([r["latency_ms"] for r in records]),
    }
    payload = {
        "meta": {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "subset": str(SUBSET_PATH),
            "provider": provider,
            "model": agent.llm.model_name if hasattr(agent.llm, "model_name") else "",
            "records": len(records),
        },
        "summary": summary,
        "records": records,
        "markdown": markdown_section(summary, records, provider),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"系统级拒答率(无证据): {summary['system_refusal_rate_unanswerable']:.1%}")
    print(f"有依据回答率: {summary['grounded_answer_rate']:.1%}")
    print(f"延迟 mean/p95: {summary['latency_ms']['mean']:.0f} / {summary['latency_ms']['p95']:.0f} ms")
    print(f"结果已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
