"""
实时监控仪表盘 — 数据管理模块
============================
DashboardManager（单例）负责：
1. 指标解析 — 将巡检文本转为结构化 JSON
2. SSE 发布/订阅 — asyncio.Queue 管理客户端连接
3. 历史缓存 — deque 保留最近 60 分钟时间序列数据

使用方式：
    from utils.dashboard import dashboard_manager
    dashboard_manager.push(raw_inspection_text)
    metrics = dashboard_manager.get_latest()
    queue = dashboard_manager.subscribe()
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import asyncio
from datetime import datetime
from collections import deque
from typing import Optional, List, Dict, Any

from utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# 指标解析器
# ============================================================================

def _extract_number(text: str) -> Optional[float]:
    """从文本中提取第一个数字（整数或浮点数）"""
    m = re.search(r"(\d+\.?\d*)", text)
    return float(m.group(1)) if m else None


def _parse_status(text: str) -> str:
    """
    从巡检文本判定状态等级。

    Returns:
        "error"  — 含 [严重] 或 [异常]
        "warning" — 含 [告警]
        "normal"  — 其他（含 [正常] 和 [信息]）
    """
    if re.search(r"\[严重\]|\[异常\]", text):
        return "error"
    if re.search(r"\[告警\]", text):
        return "warning"
    return "normal"


def _parse_ports(text: str) -> Dict:
    """解析端口检测结果"""
    items = []
    # 匹配: [状态] 端口 N (服务名): 描述...
    pattern = re.compile(
        r"\[(正常|异常)\]\s*端口\s*(\d+)\s*\(([^)]+)\)[：:]\s*(.+?)(?=\n\s*\[|\n\n|$)"
    )
    for m in pattern.finditer(text):
        status_label, port, service, detail = m.groups()
        items.append({
            "label": f"{port} {service}",
            "status": "error" if status_label == "异常" else "ok",
            "detail": detail.strip(),
        })

    if not items:
        # SSH format fallback
        for m in re.finditer(r"PORT_(OK|DOWN):(\d+)", text):
            status, port = m.groups()
            items.append({
                "label": f"{port}",
                "status": "error" if status == "DOWN" else "ok",
                "detail": "监听中" if status == "OK" else "未监听",
            })

    normal_count = sum(1 for i in items if i["status"] == "ok")
    status = _parse_status(text)
    return {
        "type": "ports",
        "name": "端口检测",
        "status": status,
        "summary": f"{normal_count}/{len(items)} 端口正常" if items else "无数据",
        "items": items,
    }


def _parse_nginx(text: str) -> Dict:
    """解析 Nginx 检测结果，兼容模拟/本机/SSH 三种格式"""
    items: list = []
    status = _parse_status(text)

    if "运行中" in text:
        summary_parts = []
        uptime_m = re.search(r"运行时长[：:]\s*(\d+)小时", text)
        conn_m = re.search(r"活跃连接数[：:]\s*(\d+)", text)
        if uptime_m:
            summary_parts.append(f"运行 {uptime_m.group(1)}h")
        if conn_m:
            summary_parts.append(f"连接 {conn_m.group(1)}")
        summary = " · ".join(summary_parts) if summary_parts else "运行中"
    elif "已停止" in text:
        summary = "已停止"
    elif "配置" in text and "错误" in text:
        summary = "配置异常"
    elif "未安装" in text or "未运行" in text:
        summary = "未安装/未运行"
    elif "NGINX_RUNNING" in text:
        summary = "运行中"
        conn = re.search(r"NGINX_CONN:(\d+)", text)
        if conn:
            summary += f" · 连接 {conn.group(1)}"
    elif "NGINX_STOPPED" in text:
        summary = "已停止"
    else:
        # 提取 [信息]/[正常]/[异常] 后的第一句
        info_m = re.search(r"\[(信息|正常|异常|告警)\]\s*(.+?)(?:\n|$)", text)
        summary = info_m.group(2).strip() if info_m else text.split("\n")[0].strip()
        # 去掉可能的 hostname 前缀
        if summary.startswith("[") and "]" in summary[:30]:
            summary = summary.split("]", 1)[-1].strip()

    return {
        "type": "nginx",
        "name": "Nginx服务",
        "status": status,
        "summary": summary,
        "items": items,
    }


def _parse_oa_service(text: str) -> Dict:
    """解析 OA 应用检测结果，兼容模拟/本机/SSH 三种格式"""
    items: list = []
    status = _parse_status(text)

    if "运行正常" in text:
        resp_m = re.search(r"HTTP响应时间[：:]\s*(\d+)ms", text)
        resp = resp_m.group(1) + "ms" if resp_m else ""
        summary = f"正常 · {resp}" if resp else "正常"
    elif "响应缓慢" in text:
        resp_m = re.search(r"HTTP响应时间[：:]\s*(\d+)ms", text)
        resp = resp_m.group(1) + "ms" if resp_m else ""
        summary = f"响应慢 · {resp}" if resp else "响应缓慢"
    elif "宕机" in text:
        summary = "疑似宕机"
    elif "未检测到" in text:
        # 本机 local 模式：未运行 Java/Tomcat
        summary = "未检测到进程"
    elif "OA_RUNNING" in text:
        summary = "运行中"
    elif "OA_STOPPED" in text:
        summary = "已停止"
    else:
        info_m = re.search(r"\[(信息|正常|异常|告警|严重)\]\s*(.+?)(?:\n|$)", text)
        summary = info_m.group(2).strip() if info_m else text.split("\n")[0].strip()
        if summary.startswith("[") and "]" in summary[:30]:
            summary = summary.split("]", 1)[-1].strip()

    return {
        "type": "oa",
        "name": "OA应用",
        "status": status,
        "summary": summary,
        "items": items,
    }


def _parse_disk(text: str) -> Dict:
    """解析磁盘检测结果，兼容两种格式：
    - 模拟/Linux: [告警] /dev/sdb1 (/数据盘): 使用率 88% (超过阈值85%)
    - 本机Windows: [告警] C:: 94.4% (200.8GB, 阈值85%)
    """
    items = []

    # 格式1: [状态] 挂载点: 使用率 N% ...
    pattern1 = re.compile(
        r"\[(正常|告警|异常)\]\s*(.+?)[：:]\s*使用率\s*(\d+\.?\d*)%"
    )
    for m in pattern1.finditer(text):
        status_label, mount, usage = m.groups()
        mount_clean = mount.replace("(", " ").replace(")", "").strip()
        label = mount_clean.split()[0] if " " in mount_clean else mount_clean
        items.append({
            "label": label,
            "status": "warning" if status_label == "告警" else ("error" if status_label == "异常" else "ok"),
            "detail": f"{usage}%",
        })

    # 格式2 (Windows): [状态] C:: 94.4% (200.8GB, 阈值85%)
    pattern2 = re.compile(
        r"\[(正常|告警|异常)\]\s*([A-Za-z]:):\s*(\d+\.?\d*)%"
    )
    for m in pattern2.finditer(text):
        status_label, drive, usage = m.groups()
        label = f"{drive}:"
        items.append({
            "label": label,
            "status": "warning" if status_label == "告警" else ("error" if status_label == "异常" else "ok"),
            "detail": f"{usage}%",
        })

    status = _parse_status(text)
    alert_items = [i for i in items if i["status"] != "ok"]
    if alert_items:
        summary = "⚠ " + " · ".join(f"{i['label']} {i['detail']}" for i in alert_items)
    elif items:
        summary = "全部正常"
    else:
        summary = "无数据"

    return {
        "type": "disk",
        "name": "磁盘使用",
        "status": status,
        "summary": summary,
        "items": items,
    }


def _parse_memory(text: str) -> Dict:
    """解析内存检测结果，兼容两种格式：
    - 模拟: [正常] 内存使用正常 \n 总内存: 32GB \n 使用率: 62% \n 可用内存: 12.2GB
    - 本机:  [正常] 内存使用率: 43.3% \n 总内存: 31.7GB | 已用: 13.8GB
    """
    items: list = []
    status = _parse_status(text)

    total_m = re.search(r"总内存[：:]\s*(\d+\.?\d*)GB", text)
    used_pct_m = re.search(r"(?:使用率|内存使用率)[：:]\s*(\d+\.?\d*)%", text)
    avail_m = re.search(r"可用内存[：:]\s*(\d+\.?\d*)GB", text)
    used_abs_m = re.search(r"已用[：:]\s*(\d+\.?\d*)GB", text)

    total_val = total_m.group(1) if total_m else ""
    pct_val = used_pct_m.group(1) if used_pct_m else ""

    if pct_val:
        summary = f"{pct_val}%"
        if total_val:
            summary += f" · {total_val}GB"
    else:
        summary = text.split("\n")[0].replace("[正常]", "").replace("[告警]", "").replace("[信息]", "").strip()

    return {
        "type": "memory",
        "name": "内存使用",
        "status": status,
        "summary": summary,
        "items": items,
    }


# 解析器注册表
_CHECK_PARSERS = {
    "ports": _parse_ports,
    "nginx": _parse_nginx,
    "oa": _parse_oa_service,
    "disk": _parse_disk,
    "memory": _parse_memory,
}

def _classify_chunk(text: str) -> Optional[str]:
    """根据文本内容判断属于哪个检测类型"""
    head = text[:120]
    if "端口" in head or "PORT_" in head:
        return "ports"
    if "Nginx" in head or "NGINX" in head:
        return "nginx"
    if "OA应用" in head or "OA_SERVICE" in head or ("OA" in head and "响应" in head):
        return "oa"
    # memory 在 disk 之前，避免"内存使用率"被 disk 的"使用率"误匹配
    if "内存" in head or "MEMORY" in head or "总内存" in head:
        return "memory"
    if "磁盘" in head or "DISK" in head or "/dev/" in head:
        return "disk"
    return None


def _split_inspection_text(raw_text: str) -> Dict[str, str]:
    """
    将完整巡检文本按检测项拆分为 5 段。

    策略（按优先级）：
    1. [HOSTNAME] XXX检测:  → 主分界（本机/SSH 格式）
    2. [状态] 开头的行       → 次分界（模拟格式）
    3. === XXX ===           → SSH 格式
    4. 去掉 AI 报告重复区（第二次出现的 ======== 之后）
    """
    chunks: Dict[str, str] = {}

    # ---- 截断：去掉 AI 报告区（重复数据）----
    # AI 报告区特征：含"详细数据"或"预警分析"或"改进策略"或"─"分隔线
    ai_cut = len(raw_text)
    for pattern in [r"详细数据", r"预警分析", r"改进策略", r"^─{10,}", r"离线模式.*无AI分析"]:
        m = re.search(pattern, raw_text, re.MULTILINE)
        if m and m.start() < ai_cut:
            ai_cut = m.start()
    raw_text = raw_text[:ai_cut]

    # ---- 找所有分界点 ----
    section_starts = []

    # [HOSTNAME] XXX检测: 格式（优先级最高）
    hostname_markers = list(re.finditer(
        r"^\[[A-Za-z0-9\-_.]+\]\s*(端口|Nginx|OA应用|磁盘|内存)\S*",
        raw_text, re.MULTILINE
    ))
    if hostname_markers:
        for m in hostname_markers:
            section_starts.append(m.start())
    else:
        # 无 hostname 标记时，用 [状态] 行
        for m in re.finditer(r"^\s*\[(正常|告警|严重|异常|信息)\]\s*", raw_text, re.MULTILINE):
            section_starts.append(m.start())
        # 也找 === XXX === 行（SSH 格式）
        for m in re.finditer(r"^=== (\w+) ===$", raw_text, re.MULTILINE):
            section_starts.append(m.start())

    section_starts.sort()

    if not section_starts:
        return chunks

    # ---- 切分并归类 ----
    for i, start in enumerate(section_starts):
        end = section_starts[i + 1] if i + 1 < len(section_starts) else len(raw_text)
        chunk_text = raw_text[start:end].strip()
        ctype = _classify_chunk(chunk_text)
        if ctype:
            if ctype in chunks:
                chunks[ctype] += "\n" + chunk_text
            else:
                chunks[ctype] = chunk_text

    return chunks


def _extract_timestamp(raw_text: str) -> str:
    """从巡检文本中提取时间戳"""
    m = re.search(r"巡检时间[：:]\s*(.+)$", raw_text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _extract_alerts(checks: List[Dict]) -> List[Dict]:
    """从检测结果中提取告警列表"""
    alerts = []
    now = datetime.now().strftime("%H:%M:%S")
    for check in checks:
        if check["status"] == "error":
            alerts.append({
                "severity": "error",
                "title": f"{check['name']}: {check['summary']}",
                "time": now,
            })
        elif check["status"] == "warning":
            alerts.append({
                "severity": "warning",
                "title": f"{check['name']}: {check['summary']}",
                "time": now,
            })
    return alerts


def parse_inspection_to_metrics(raw_text: str) -> Dict[str, Any]:
    """
    将完整巡检文本解析为结构化指标 JSON。

    Args:
        raw_text: run_unified_inspection() 返回的完整巡检报告

    Returns:
        结构化指标字典，见设计文档
    """
    chunks = _split_inspection_text(raw_text)
    checks = []

    for check_type, parser in _CHECK_PARSERS.items():
        chunk_text = chunks.get(check_type, "")
        if chunk_text:
            try:
                parsed = parser(chunk_text)
                checks.append(parsed)
            except Exception as e:
                logger.warning(f"解析 {check_type} 失败: {e}")
                # 降级：将原始文本作为摘要
                first_line = chunk_text.strip().split("\n")[0] if chunk_text.strip() else "无数据"
                checks.append({
                    "type": check_type,
                    "name": {"ports": "端口检测", "nginx": "Nginx服务", "oa": "OA应用",
                             "disk": "磁盘使用", "memory": "内存使用"}.get(check_type, check_type),
                    "status": _parse_status(chunk_text),
                    "summary": first_line[:80],
                    "items": [],
                })

    status_counts = {"normal": 0, "warning": 0, "error": 0}
    for c in checks:
        s = c["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    alerts = _extract_alerts(checks)
    timestamp = _extract_timestamp(raw_text)

    return {
        "timestamp": timestamp,
        "mode": "simulated" if "模拟" in raw_text[:200] else ("ssh" if "SSH" in raw_text[:200] else "local"),
        "summary": {
            "total_checks": len(checks),
            "normal": status_counts["normal"],
            "warning": status_counts["warning"],
            "error": status_counts["error"],
        },
        "checks": checks,
        "alerts": alerts,
    }


# ============================================================================
# 时间序列数据点提取
# ============================================================================

def _extract_timeseries_point(checks: List[Dict], timestamp: str) -> Dict:
    """
    从 checks 中提取关键数值，生成一个可用于趋势图的数据点。

    Returns:
        {"time": "14:30:00", "disk_sda1": 45, "disk_sdb1": 88, "memory_pct": 62, ...}
    """
    point: Dict[str, Any] = {"time": timestamp[-8:] if len(timestamp) >= 8 else timestamp}

    for check in checks:
        for item in check.get("items", []):
            pct = _extract_number(item.get("detail", ""))
            if pct is not None and pct <= 100:
                key = item["label"].replace(" ", "_").replace("/", "_").lower()
                point[key] = pct

    # 内存特殊处理
    mem_check = next((c for c in checks if c["type"] == "memory"), None)
    if mem_check:
        mem_pct = _extract_number(mem_check.get("summary", ""))
        if mem_pct is not None and mem_pct <= 100:
            point["memory_pct"] = mem_pct

    return point


# ============================================================================
# DashboardManager 单例
# ============================================================================

class DashboardManager:
    """
    实时监控仪表盘数据管理器（单例）。

    职责：
    - 接收巡检文本 → 解析为结构化指标 → 推送给所有 SSE 订阅者
    - 维护最近 60 分钟的时间序列历史
    - 管理 SSE 订阅者队列
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._subscribers: list[asyncio.Queue] = []
        self._latest_metrics: Optional[Dict[str, Any]] = None
        self._history: deque = deque(maxlen=120)  # 30s × 120 = 60min
        logger.info("DashboardManager 初始化完成")

    # ---- 发布 ----

    def push(self, raw_text: str):
        """
        接收一次巡检的原始文本，解析后推送给所有订阅者。

        Args:
            raw_text: run_unified_inspection() 返回的完整巡检报告
        """
        try:
            metrics = parse_inspection_to_metrics(raw_text)
        except Exception as e:
            logger.error(f"指标解析失败: {e}")
            return

        self._latest_metrics = metrics

        # 提取时间序列数据点
        ts_point = _extract_timeseries_point(
            metrics.get("checks", []),
            metrics.get("timestamp", "")
        )
        self._history.append(ts_point)

        # 广播给所有订阅者
        dead: list = []
        for q in self._subscribers:
            try:
                q.put_nowait(metrics)
            except asyncio.QueueFull:
                # 队列满了，丢弃本次推送（订阅者消费太慢）
                pass
            except Exception:
                dead.append(q)

        for q in dead:
            self._subscribers.remove(q)

        logger.debug(f"指标已推送至 {len(self._subscribers)} 个订阅者")

    # ---- 订阅 ----

    def subscribe(self) -> asyncio.Queue:
        """
        创建新的 SSE 订阅队列。

        Returns:
            asyncio.Queue，最大容量 16（防止慢客户端拖垮服务端）
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=16)
        self._subscribers.append(q)
        logger.info(f"新增 SSE 订阅者，当前共 {len(self._subscribers)} 个")
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """移除订阅队列（客户端断开时调用）"""
        try:
            self._subscribers.remove(q)
            logger.info(f"移除 SSE 订阅者，当前共 {len(self._subscribers)} 个")
        except ValueError:
            pass

    # ---- 查询 ----

    def get_latest(self) -> Optional[Dict[str, Any]]:
        """返回最新一次巡检的结构化指标"""
        return self._latest_metrics

    def get_history(self, minutes: int = 60) -> List[Dict]:
        """
        返回最近 N 分钟的时间序列数据。

        Args:
            minutes: 回溯时长（分钟）

        Returns:
            时间序列数据点列表，已按时间排序
        """
        max_points = minutes * 2  # 按 30s 间隔估算
        points = list(self._history)[-max_points:]
        return points


# 全局单例
dashboard_manager = DashboardManager()


# ============================================================================
# 自测
# ============================================================================

if __name__ == "__main__":
    # 模拟一段完整巡检输出（与实际 run_unified_inspection 输出一致）
    SAMPLE_TEXT = """=======================================================
OA系统巡检报告（模拟数据）
巡检时间: 2026-06-15 14:30:00
巡检模式: 模拟数据
=======================================================

端口检测完成，状态: 正常
  [正常] 端口 80 (HTTP服务): 监听中，延迟 0.5ms
  [正常] 端口 443 (HTTPS服务): 监听中，延迟 0.8ms
  [正常] 端口 8080 (OA应用端口): 监听中，延迟 1.2ms
  [正常] 端口 3306 (MySQL数据库): 监听中，延迟 0.3ms
  [正常] 端口 6379 (Redis缓存): 监听中，延迟 0.6ms

[正常] Nginx服务运行中
  运行时长: 120小时
  活跃连接数: 234
  配置文件语法: OK

[告警] OA应用服务响应缓慢！
  HTTP响应时间: 4500ms (超过阈值2000ms)
  JVM堆内存使用率: 92%
  建议: 检查数据库连接池、JVM GC日志、慢SQL

磁盘检测完成 —— 存在磁盘告警，请及时清理或扩容！
  [正常] /dev/sda1 (/根目录): 使用率 45%
  [告警] /dev/sdb1 (/数据盘): 使用率 88% (超过阈值85%)

[正常] 内存使用正常
  总内存: 32GB
  使用率: 62%
  可用内存: 12.2GB
=======================================================
提示: 模拟模式使用随机数据。配置 SSH 或切换 local 模式启用真实检测。
"""

    print("=" * 60)
    print("DashboardManager Self-Test")
    print("=" * 60)

    # 测试指标解析
    import json
    metrics = parse_inspection_to_metrics(SAMPLE_TEXT)
    print("\n[metrics] Parsed result:")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    # 测试时间序列提取
    print("\n[timeseries] Data point:")
    ts = _extract_timeseries_point(metrics["checks"], metrics["timestamp"])
    print(json.dumps(ts, ensure_ascii=False, indent=2))

    # 测试 DashboardManager
    print("\n[push/subscribe] Testing DashboardManager:")
    dm = DashboardManager()
    dm.push(SAMPLE_TEXT)

    latest = dm.get_latest()
    print(f"  get_latest() → timestamp: {latest['timestamp'] if latest else 'None'}")
    print(f"  summary: {latest['summary'] if latest else 'None'}")

    history = dm.get_history(60)
    print(f"  history 数据点: {len(history)}")

    print("\n[OK] Self-test completed")
