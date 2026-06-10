"""
OA运维多智能Agent巡检问答系统 - 主入口 v2.2
============================================
启动方式:
    python main.py              # 启动Gradio Web界面（完整功能）
    python main.py --demo       # 演示模式（不需要API密钥）
    python main.py --cli        # 命令行交互模式

配置方式:
    1. 复制 .env.example 为 .env，填入实际密钥
    2. 编辑 config.yaml 调整其他参数
"""

import os
import sys
import atexit
import argparse

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from utils.config import config
from utils.logger import get_logger

_main_logger = get_logger("main")


def _cleanup():
    """进程退出时释放所有资源"""
    _main_logger.info("正在清理资源...")
    try:
        from utils.database import db
        db.close_all()
    except Exception:
        pass
    _main_logger.info("清理完成")


atexit.register(_cleanup)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="OA运维多智能Agent巡检问答系统 v2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                # 启动Web界面（默认）
  python main.py --port 8080    # 指定端口启动
  python main.py --demo         # 演示模式（离线，不需要API密钥）
  python main.py --cli          # 命令行交互模式
  python main.py --no-auth      # 跳过登录认证
        """
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help=f"Web界面端口号（默认: {config.get('server.port', 7860)}）"
    )
    parser.add_argument(
        "--host", type=str, default=None,
        help=f"绑定的IP地址（默认: {config.get('server.host', '127.0.0.1')}）"
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="演示模式：仅展示离线巡检和日志分析功能，不需要API密钥"
    )
    parser.add_argument(
        "--cli", action="store_true",
        help="命令行交互模式（不启动Web界面）"
    )
    parser.add_argument(
        "--share", action="store_true",
        help="生成Gradio公网分享链接"
    )
    parser.add_argument(
        "--no-auth", action="store_true",
        help="跳过登录认证（仅用于开发调试）"
    )
    return parser.parse_args()


def run_cli_mode():
    """命令行交互模式"""
    print("\n" + "=" * 55)
    print("  OA运维多智能Agent巡检问答系统 - 命令行模式")
    print("=" * 55)
    print("输入 'inspect' 执行巡检")
    print("输入 'log <文件路径>' 分析日志文件")
    print("输入 'log' 粘贴日志内容分析")
    print("输入 'kb <问题>' 知识库问答")
    print("输入 'exit' 退出")
    print("=" * 55 + "\n")

    # 从配置读取 LLM 参数
    api_key = config.get("llm.api_key")
    api_base = config.get("llm.base_url")
    api_model = config.get("llm.model")

    from agents.inspection_agent import InspectionAgent
    from agents.log_analysis_agent import LogAnalysisAgent

    if api_key:
        insp_agent = InspectionAgent(api_key, api_base, api_model)
        log_agent = LogAnalysisAgent(api_key, api_base, api_model)
        print("✓ Agent已初始化（使用API模式）\n")
    else:
        insp_agent = None
        log_agent = None
        print("⚠ 未配置API密钥，使用离线模式\n")

    while True:
        try:
            user_input = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue

        if user_input.lower() == "exit":
            print("再见！")
            break
        elif user_input.lower() == "inspect":
            if insp_agent:
                print(insp_agent.run_inspection())
            else:
                from agents.inspection_agent import run_unified_inspection
                print(run_unified_inspection())
        elif user_input.lower().startswith("log "):
            file_path = user_input[4:].strip()
            if log_agent:
                print(log_agent.analyze_file(file_path))
            else:
                from agents.log_analysis_agent import scan_log_file
                print(scan_log_file.invoke({"file_path": file_path}))
        elif user_input.lower() == "log":
            print("请粘贴日志内容（以 END 结束输入）:")
            log_lines = []
            while True:
                line = input()
                if line.strip() == "END":
                    break
                log_lines.append(line)
            log_text = "\n".join(log_lines)
            if log_agent:
                print(log_agent.analyze(log_text))
            else:
                from agents.log_analysis_agent import analyze_log_content
                print(analyze_log_content.invoke({"log_text": log_text}))
        elif user_input.lower().startswith("kb "):
            print("知识库问答需要Web界面支持（需要加载嵌入模型），请使用Web模式。")
        else:
            print("未知命令。支持: inspect / log / kb / exit")


def run_demo_mode():
    """演示模式：FastAPI + 纯HTML/CSS/JS，无需API密钥"""
    print("启动演示模式（离线功能）...")
    print("可用功能: 离线巡检 / 正则日志分析 / 诊断工具箱")
    print("限制: 无LLM智能汇总 / 无知识库问答\n")
    from ui.server import run_server
    run_server(host="127.0.0.1", port=7860)


def main():
    """主入口"""
    args = parse_args()

    # 确定端口和主机
    port = args.port or config.get("server.port", 7860)
    host = args.host or config.get("server.host", "127.0.0.1")
    share = args.share or config.get("server.share", False)

    print(f"""
╔══════════════════════════════════════════════════╗
║     OA运维多智能Agent巡检问答系统 v2.2           ║
║     基于 FastAPI + LangChain + RAG + Chroma        ║
╚══════════════════════════════════════════════════╝
""")

    if args.cli:
        run_cli_mode()
    elif args.demo:
        run_demo_mode()
    else:
        # Web模式 — FastAPI + 纯HTML/CSS/JS
        from ui.server import run_server

        api_key = config.get("llm.api_key")
        if api_key:
            print(f"🔑 LLM 已配置（{config.get('llm.provider')}/{config.get('llm.model')}）")
        else:
            print("⚠️ 未配置 LLM API Key，知识库问答功能不可用")
            print("   请编辑 .env 文件填入 OA_LLM_API_KEY")

        print(f"启动Web界面: http://{host}:{port}")
        print("按 Ctrl+C 停止服务\n")

        run_server(host=host, port=port)


if __name__ == "__main__":
    main()
