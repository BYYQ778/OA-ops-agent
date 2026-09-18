"""第 4 周 /api/v1 路由：根因诊断、结构化检索、评测结果与最小指标。

- 只读：/api/v1/incidents/analyze 仅产出诊断报告并落库，不执行任何修复动作。
- 挂载于 create_app()；legacy /api/* 兼容层保持不变。
- 同步端点用普通 def（FastAPI 线程池执行），避免阻塞事件循环（第 2 周 P1 修复同款姿势）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from agents.incident_agent import analyze_incident
from agents.incident_kb import RetrievalKbAdapter
from utils.database import db
from utils.metrics import metrics
from utils.tracing import span as tracing_span

#: 评测结果目录（源码运行 = 仓库 evals/results；冻结版缺失 → available=false）
RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"


class IncidentAnalyzeRequest(BaseModel):
    """POST /api/v1/incidents/analyze 请求体。"""

    log_text: str = ""
    inspection_results: List[Dict[str, Any]] = Field(default_factory=list)
    alerts: List[Dict[str, Any]] = Field(default_factory=list)
    use_latest_inspection: bool = False
    service: str = ""
    host: str = ""
    include_llm: bool = False


class RagQueryRequest(BaseModel):
    """POST /api/v1/rag/query 请求体。"""

    question: str
    top_k: int = 5


def _latest_inspection_results(limit: int = 8) -> List[Dict[str, Any]]:
    """取最近一次巡检的记录（「一键根因分析」免于重复粘贴数据）。"""
    try:
        rows = db.get_inspection_history(days=1) or []
    except Exception:  # noqa: BLE001 - 巡检数据缺失不阻断诊断
        return []
    return [dict(row) for row in rows][:limit]


def _build_llm_narrator(kb_agent: Any) -> Optional[Callable[[str], str]]:
    """从 KB Agent 的 LLM 客户端构建受限叙述器；不可用时返回 None。"""
    llm = getattr(kb_agent, "llm", None)
    if llm is None:
        return None

    def narrator(prompt: str) -> str:
        response = llm.invoke(prompt)
        return str(getattr(response, "content", response))

    return narrator


def create_incidents_router(kb_agent_factory: Optional[Callable[[], Any]] = None) -> APIRouter:
    """构建 /api/v1 路由；kb_agent_factory 返回 KnowledgeBaseAgent 或 None（测试注入 fake）。"""
    router = APIRouter(prefix="/api/v1")

    @router.post("/incidents/analyze")
    def analyze_incident_endpoint(payload: IncidentAnalyzeRequest) -> Dict[str, Any]:
        """一键根因分析（只读）：产出报告并落库；可选 LLM 叙述（失败自动降级）。"""
        inspection = list(payload.inspection_results)
        if payload.use_latest_inspection and not inspection:
            inspection = _latest_inspection_results()

        kb_agent = kb_agent_factory() if kb_agent_factory is not None else None
        retriever = None
        if kb_agent is not None and getattr(kb_agent, "retriever", None) is not None:
            retriever = RetrievalKbAdapter(kb_agent.retriever)

        narrator = None
        if payload.include_llm and kb_agent is not None:
            narrator = _build_llm_narrator(kb_agent)

        with tracing_span("incident.analyze", {"incident.service": payload.service, "incident.host": payload.host}):
            report = analyze_incident(
                log_text=payload.log_text,
                inspection_results=inspection or None,
                alerts=payload.alerts or None,
                service=payload.service,
                host=payload.host,
                retriever=retriever,
                llm_narrator=narrator,
            )
        if payload.include_llm and narrator is None:
            report.meta["llm_note"] = "LLM 叙述不可用（未配置或知识库引擎未就绪），已返回确定性报告"

        record = report.model_dump(mode="json")
        db.save_incident(record, service=payload.service, host=payload.host, source="api")

        metrics.inc("oa_incidents_analyze_total")
        if record["status"] == "ok":
            metrics.inc("oa_incidents_ok_total")
        else:
            metrics.inc("oa_incidents_uncertain_total")
        return {"saved": True, "report": record}

    @router.get("/incidents")
    def list_incidents_endpoint(limit: int = 50) -> Dict[str, Any]:
        """诊断记录摘要列表。"""
        bounded = max(1, min(int(limit), 200))
        return {"items": db.list_incidents(limit=bounded)}

    @router.get("/incidents/{incident_id}")
    def get_incident_endpoint(incident_id: str) -> Dict[str, Any]:
        """按编号取诊断报告（含完整 report/events JSON）。"""
        record = db.get_incident(incident_id)
        if record is None:
            raise HTTPException(status_code=404, detail="诊断记录不存在")
        return record

    @router.post("/rag/query")
    def rag_query_endpoint(payload: RagQueryRequest) -> Dict[str, Any]:
        """结构化检索（不调用 LLM）；知识库引擎未就绪时 available=false。"""
        metrics.inc("oa_rag_query_total")
        kb_agent = kb_agent_factory() if kb_agent_factory is not None else None
        if kb_agent is None:
            return {
                "available": False,
                "message": "知识库引擎未就绪（初始化中或不可用）",
                "hits": [],
                "citations": [],
                "refused": False,
            }
        top_k = max(1, min(int(payload.top_k), 20))
        with tracing_span("rag.query", {"rag.question_len": len(payload.question), "rag.top_k": top_k}):
            outcome = kb_agent.retrieve(payload.question, top_k=top_k)
        if outcome.get("refused"):
            metrics.inc("oa_rag_query_refused_total")
        return outcome

    @router.get("/evals/latest")
    def evals_latest_endpoint() -> Dict[str, Any]:
        """最近评测结果（RAG + 根因诊断案例）；结果文件缺失时 available=false。

        RAG 结果优先取运行时 latest.json；缺失（如全新检出）时回退到已入库的
        rag-eval-final-all.json。
        """
        outcome: Dict[str, Any] = {"available": False, "rag": None, "incidents": None, "files": {}}
        candidates: Dict[str, tuple] = {
            "rag": ("latest.json", "rag-eval-final-all.json"),
            "incidents": ("incidents-v1.json",),
        }
        for key, filenames in candidates.items():
            for filename in filenames:
                path = RESULTS_DIR / filename
                if not path.exists():
                    continue
                try:
                    outcome[key] = json.loads(path.read_text(encoding="utf-8"))
                    outcome["available"] = True
                    outcome["files"][key] = filename
                except (OSError, json.JSONDecodeError):
                    continue
                break
        return outcome

    @router.get("/metrics")
    def metrics_endpoint() -> PlainTextResponse:
        """最小 Prometheus 文本指标（第 5 周扩展）。"""
        return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")

    return router
