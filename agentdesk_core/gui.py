"""AgentDesk 的兼容桌面界面。"""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk
from typing import Any, Dict, Optional

from .agent import AgentDesk
from .config import Settings


class ApprovalRequest:
    def __init__(self, action: str, plan: Dict[str, Any]) -> None:
        self.action = action
        self.plan = plan
        self.approved = False
        self.done = threading.Event()


class DesktopApproval:
    """把 Agent 工作线程的确认请求交给 Tk 主线程显示弹窗。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.requests: "queue.Queue[ApprovalRequest]" = queue.Queue()
        self.root.after(100, self.process_requests)

    def confirm(self, action: str, plan: Dict[str, Any]) -> bool:
        request = ApprovalRequest(action, plan)
        self.requests.put(request)
        request.done.wait()
        return request.approved

    def process_requests(self) -> None:
        try:
            while True:
                request = self.requests.get_nowait()
                request.approved = self.ask_user(request)
                request.done.set()
        except queue.Empty:
            pass
        self.root.after(100, self.process_requests)

    def ask_user(self, request: ApprovalRequest) -> bool:
        return False


class AgentWindow:
    def __init__(self, root: tk.Tk, settings: Settings) -> None:
        self.root = root
        self.root.title("AgentDesk")
        self.root.geometry("900x650")
        self.root.minsize(640, 480)
        self.busy = False
        self.approval = DesktopApproval(root)
        self.agent = AgentDesk(settings, confirm=self.approval.confirm)

        self.transcript = scrolledtext.ScrolledText(root, wrap=tk.WORD, state=tk.DISABLED)
        self.transcript.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 8))

        bottom = ttk.Frame(root)
        bottom.pack(fill=tk.X, padx=12, pady=(0, 12))
        self.input_box = ttk.Entry(bottom)
        self.input_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_box.bind("<Return>", self.send)
        self.send_button = ttk.Button(bottom, text="发送", command=self.send)
        self.send_button.pack(side=tk.LEFT, padx=(8, 0))

        self.status = ttk.Label(root, text="就绪")
        self.status.pack(anchor=tk.W, padx=12, pady=(0, 8))
        self.write_message("系统", "AgentDesk 已启动。当前支持查询服务器运行信息。")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.input_box.focus_set()

    def write_message(self, speaker: str, text: str) -> None:
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, f"{speaker}：\n{text}\n\n")
        self.transcript.see(tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def send(self, _event: Optional[tk.Event] = None) -> str:
        if self.busy:
            return "break"
        question = self.input_box.get().strip()
        if not question:
            return "break"
        self.input_box.delete(0, tk.END)
        self.write_message("你", question)
        self.busy = True
        self.send_button.configure(state=tk.DISABLED)
        self.status.configure(text="Agent 工作中……")
        threading.Thread(target=self.run_agent, args=(question,), daemon=True).start()
        return "break"

    def run_agent(self, question: str) -> None:
        try:
            answer = self.agent.run_turn(question)
        except Exception as error:
            answer = f"执行失败：{error}"
        self.root.after(0, self.finish_turn, answer)

    def finish_turn(self, answer: str) -> None:
        self.write_message("助手", answer)
        self.busy = False
        self.send_button.configure(state=tk.NORMAL)
        self.status.configure(text="就绪")
        self.input_box.focus_set()

    def close(self) -> None:
        if self.busy:
            should_close = messagebox.askyesno("正在执行", "Agent 还在工作，确认退出吗？", parent=self.root)
            if not should_close:
                return
        self.agent.close()
        self.root.destroy()


def main() -> None:
    from .config import load_settings

    root = tk.Tk()
    try:
        settings = load_settings()
    except RuntimeError as error:
        root.withdraw()
        messagebox.showerror("配置错误", str(error), parent=root)
        root.destroy()
        return
    AgentWindow(root, settings)
    root.mainloop()
