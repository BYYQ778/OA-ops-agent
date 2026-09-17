"""可选重排序组件测试（完全离线，注入 fake encoder）。

覆盖：按分重排、top_k 截断、encoder 异常直通、加载失败只尝试一次、
未启用返回 None、无候选项边界。
"""

from utils.retrieval import RetrievalHit
from utils.rerank import CrossEncoderReranker, build_reranker


def _hit(hid: str, text: str) -> RetrievalHit:
    return RetrievalHit(id=hid, text=text, metadata={})


class _FakeEncoder:
    def __init__(self, scores=None, raises=False):
        self.scores = scores or []
        self.raises = raises
        self.calls = 0

    def predict(self, pairs):
        self.calls += 1
        if self.raises:
            raise RuntimeError("encoder boom")
        return self.scores


def test_reranker_reorders_by_score() -> None:
    hits = [_hit("a", "低相关"), _hit("b", "高相关"), _hit("c", "中相关")]
    enc = _FakeEncoder(scores=[0.1, 0.9, 0.5])
    out = CrossEncoderReranker(encoder=enc)("查询", hits, top_k=3)
    assert [h.id for h in out] == ["b", "c", "a"]


def test_reranker_respects_top_k() -> None:
    hits = [_hit("a", "x"), _hit("b", "y"), _hit("c", "z")]
    enc = _FakeEncoder(scores=[0.2, 0.8, 0.5])
    out = CrossEncoderReranker(encoder=enc)("查询", hits, top_k=2)
    assert [h.id for h in out] == ["b", "c"]


def test_reranker_empty_candidates() -> None:
    assert CrossEncoderReranker(encoder=_FakeEncoder())("查询", [], top_k=5) == []


def test_reranker_encoder_failure_degrades_to_original_order() -> None:
    hits = [_hit("a", "x"), _hit("b", "y")]
    enc = _FakeEncoder(raises=True)
    out = CrossEncoderReranker(encoder=enc)("查询", hits, top_k=5)
    assert [h.id for h in out] == ["a", "b"]


def test_reranker_load_failure_passthrough_and_single_attempt() -> None:
    # 不注入 encoder：CI/离线环境 sentence-transformers 不可用 → 直通
    reranker = CrossEncoderReranker(model_name="不存在的模型-xyz")
    hits = [_hit("a", "x"), _hit("b", "y")]
    first = reranker("查询", hits, top_k=5)
    assert [h.id for h in first] == ["a", "b"]
    assert reranker._load_failed is True
    # 第二次仍直通（不再尝试加载）
    second = reranker("查询", hits, top_k=1)
    assert [h.id for h in second] == ["a"]


def test_build_reranker_disabled_returns_none() -> None:
    assert build_reranker(enabled=False) is None


def test_build_reranker_enabled_returns_callable() -> None:
    reranker = build_reranker(enabled=True, model_name="some-model")
    assert reranker is not None and callable(reranker)
    assert reranker.model_name == "some-model"


def test_reranker_pairs_query_with_each_text() -> None:
    seen = {}

    class _CapturingEncoder:
        def predict(self, pairs):
            seen["pairs"] = list(pairs)
            return [0.5] * len(pairs)

    hits = [_hit("a", "文本甲"), _hit("b", "文本乙")]
    CrossEncoderReranker(encoder=_CapturingEncoder())("问题Q", hits, top_k=2)
    assert seen["pairs"] == [("问题Q", "文本甲"), ("问题Q", "文本乙")]
