"""AgentDesk 命令行入口。"""

from .agent import AgentDesk
from .config import load_settings


def main() -> None:
    settings = load_settings()
    agent = AgentDesk(settings)
    print("AgentDesk 已启动。")
    print("当前支持：查询服务器运行信息。")
    print(f"服务器：{settings.server_host}:{settings.server_port}")
    print("输入 exit 或 quit 退出。")
    try:
        while True:
            question = input("\n你：").strip()
            if question.lower() in {"exit", "quit", "退出"}:
                print("已退出。")
                break
            if question:
                print("助手：", agent.run_turn(question))
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        agent.close()
