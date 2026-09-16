from agents.log_analysis_agent import analyze_log_content


def analyze(text: str) -> str:
    return analyze_log_content.invoke({"log_text": text})


def test_empty_log_returns_helpful_prompt() -> None:
    assert "日志内容为空" in analyze("")


def test_502_log_is_classified() -> None:
    report = analyze("2026-09-16 ERROR 502 Bad Gateway upstream unavailable")
    assert "502 Bad Gateway" in report


def test_oom_log_is_classified() -> None:
    report = analyze("java.lang.OutOfMemoryError: Java heap space")
    assert "OOM内存溢出" in report
