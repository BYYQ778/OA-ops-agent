"""
告警通知模块
-----------
巡检发现异常时，通过邮件发送告警通知。
支持: QQ邮箱 SMTP / 钉钉 Webhook / 企业微信 Webhook

使用方式:
    from utils.alert import AlertManager
    alert = AlertManager()
    alert.send_email("磁盘告警", "C盘使用率 96.4%，超过阈值")
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from datetime import datetime
from typing import List, Dict

from utils.config import config
from utils.logger import get_logger
from utils.database import db

logger = get_logger(__name__)


class AlertManager:
    """告警管理器"""

    def __init__(self):
        self.enabled = config.get("alert.enabled", False)

    def check_and_alert(self, inspection_results: List[Dict]):
        """
        检查巡检结果，如有异常则发送告警。

        Args:
            inspection_results: [{"check_type": "disk", "status": "warning", ...}, ...]
        """
        if not self.enabled:
            logger.info("告警功能已禁用")
            return

        # 筛选异常项
        warnings = [r for r in inspection_results if r.get("status") == "warning"]
        errors = [r for r in inspection_results if r.get("status") == "error"]

        if not warnings and not errors:
            return  # 一切正常，无需告警

        # 构建告警标题
        total_issues = len(warnings) + len(errors)
        hostname = inspection_results[0].get("target", "未知主机") if inspection_results else "OA系统"
        title = f"[{hostname}] 巡检发现 {total_issues} 项异常"

        # 构建告警详情
        detail_lines = [
            f"巡检时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"主机: {hostname}",
            f"异常项: {total_issues} (严重: {len(errors)}, 告警: {len(warnings)})",
            "",
            "--- 异常详情 ---",
        ]
        for r in errors:
            detail_lines.append(f"  ❌ [{r['check_type_cn']}] {r['result'][:200]}")
        for r in warnings:
            detail_lines.append(f"  ⚠️ [{r['check_type_cn']}] {r['result'][:200]}")

        detail_lines.append("")
        detail_lines.append(f"详情请查看: http://{config.get('server.host', '127.0.0.1')}:{config.get('server.port', 7860)}")

        detail = "\n".join(detail_lines)

        # 记录告警到数据库
        severity = "critical" if errors else "warning"
        alert_id = db.save_alert("inspection", severity, title, detail)

        # 发送通知
        self._send_all(title, detail)

        # 标记已通知
        db.mark_alert_notified(alert_id, "email")

    def _send_all(self, title: str, detail: str):
        """尝试所有启用的通知渠道"""
        if config.get("alert.email.enabled"):
            self.send_email(title, detail)
        if config.get("alert.dingtalk.enabled"):
            self.send_dingtalk(title, detail)
        if config.get("alert.wecom.enabled"):
            self.send_wecom(title, detail)

    def send_email(self, subject: str, body: str) -> bool:
        """
        发送邮件告警。

        Args:
            subject: 邮件主题
            body: 邮件正文

        Returns:
            是否发送成功
        """
        smtp_host = config.get("alert.email.smtp_host", "")
        smtp_port = config.get("alert.email.smtp_port", 587)
        smtp_user = config.get("alert.email.smtp_user", "")
        smtp_password = config.get("alert.email.smtp_password", "")
        to_list = config.get("alert.email.to", [])

        if not all([smtp_host, smtp_user, smtp_password, to_list]):
            logger.warning("邮件配置不完整，跳过邮件告警")
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = Header(f"OA运维系统 <{smtp_user}>")
            msg["To"] = Header(", ".join(to_list))
            msg["Subject"] = Header(subject, "utf-8")
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, to_list, msg.as_string())

            logger.info(f"邮件告警已发送: {subject} → {', '.join(to_list)}")
            return True

        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return False

    def send_dingtalk(self, title: str, content: str) -> bool:
        """发送钉钉机器人通知"""
        webhook = config.get("alert.dingtalk.webhook_url", "")
        if not webhook:
            return False

        try:
            import requests
            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": f"## {title}\n\n{content}"
                }
            }
            resp = requests.post(webhook, json=data, timeout=10)
            if resp.status_code == 200:
                logger.info("钉钉告警已发送")
                return True
            else:
                logger.warning(f"钉钉发送失败: {resp.text}")
                return False
        except ImportError:
            logger.warning("requests 未安装，无法发送钉钉通知")
            return False
        except Exception as e:
            logger.error(f"钉钉发送异常: {e}")
            return False

    def send_wecom(self, title: str, content: str) -> bool:
        """发送企业微信机器人通知"""
        webhook = config.get("alert.wecom.webhook_url", "")
        if not webhook:
            return False

        try:
            import requests
            data = {
                "msgtype": "markdown",
                "markdown": {
                    "content": f"## {title}\n{content}"
                }
            }
            resp = requests.post(webhook, json=data, timeout=10)
            if resp.status_code == 200:
                logger.info("企业微信告警已发送")
                return True
            else:
                logger.warning(f"企业微信发送失败: {resp.text}")
                return False
        except ImportError:
            logger.warning("requests 未安装，无法发送企业微信通知")
            return False
        except Exception as e:
            logger.error(f"企业微信发送异常: {e}")
            return False

    def send_test_email(self) -> str:
        """发送测试邮件（用于验证配置）"""
        to_list = config.get("alert.email.to", [])
        result = self.send_email(
            "[OA运维系统] 测试邮件",
            f"这是一封来自 OA运维Agent系统 的测试邮件\n\n"
            f"发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"收件人: {', '.join(to_list)}\n\n"
            f"如果你收到这封邮件，说明邮件告警配置正确。"
        )
        return "✅ 测试邮件发送成功" if result else "❌ 发送失败，请检查 .env 中的 OA_EMAIL_PASSWORD"


# 全局单例
alert = AlertManager()
