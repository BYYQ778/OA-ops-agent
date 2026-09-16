from utils.dashboard import parse_inspection_to_metrics


def test_parse_inspection_text_into_dashboard_metrics() -> None:
    report = """巡检时间: 2026-09-16 09:30:00
[server01] 端口检测:
[正常] 端口 80 正常监听
[server01] Nginx检测:
[正常] Nginx 服务运行正常
[server01] OA应用检测:
[告警] OA应用响应时间 2500ms
[server01] 磁盘检测:
[告警] /dev/sdb1 (/数据盘): 使用率 88%
[server01] 内存检测:
[正常] 总内存 8192MB, 已用 4096MB, 使用率 50%
"""

    metrics = parse_inspection_to_metrics(report)

    assert metrics["timestamp"] == "2026-09-16 09:30:00"
    assert metrics["summary"]["total_checks"] == 5
    assert metrics["summary"]["warning"] == 2
    assert {check["type"] for check in metrics["checks"]} == {
        "ports", "nginx", "oa", "disk", "memory"
    }
