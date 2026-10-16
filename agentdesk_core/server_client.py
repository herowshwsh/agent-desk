"""受限的 SSH 服务器只读查询工具。"""

import re
import shlex
from typing import Any, Callable, Dict

import paramiko


class ServerClient:
    """通过固定的只读命令获取服务器运行状态。"""

    COMMANDS = {
        "主机名": "hostname",
        "操作系统": "uname -srmo",
        "运行时间": "uptime",
        "CPU 核数": "nproc 2>/dev/null || getconf _NPROCESSORS_ONLN",
        "内存": "free -h 2>/dev/null || vmstat -s 2>/dev/null | head -n 5",
        "磁盘": "df -h / | tail -n 1",
        "内网地址": "hostname -I 2>/dev/null | awk '{print $1}'",
    }
    SERVICES_COMMAND = """
if command -v systemctl >/dev/null 2>&1; then
  systemctl list-units --type=service --state=running --no-pager --no-legend
elif command -v service >/dev/null 2>&1; then
  service --status-all 2>&1 | sed -n '/\\[ + \\]/p'
else
  ps -eo pid,comm,args --sort=comm
fi
""".strip()
    PM2_COMMAND = """
if command -v pm2 >/dev/null 2>&1; then
  pm2 jlist 2>&1 || pm2 list
else
  echo "未找到 pm2 命令"
fi
""".strip()
    PM2_OPERATIONS = {
        "pm2_start": "start",
        "pm2_stop": "stop",
        "pm2_restart": "restart",
    }

    def __init__(self, host: str, username: str, password: str, port: int = 22, timeout: float = 10.0) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout

    def test_connection(self) -> Dict[str, Any]:
        """设置页的测试连接：只验证 SSH 能否登录，返回耗时。"""
        import time

        started = time.perf_counter()
        result = self._read(lambda client: {"状态": "连接成功"})
        elapsed = round((time.perf_counter() - started) * 1000)
        if result.get("ok"):
            result["message"] = f"连接成功（{elapsed} ms）"
        return result

    def query(self) -> Dict[str, Any]:
        return self._read(lambda client: {"连接地址": f"{self.host}:{self.port}", "登录用户": self.username, **{
            label: self._run(client, command) for label, command in self.COMMANDS.items()
        }})

    def query_services(self) -> Dict[str, Any]:
        return self._read(lambda client: {
            "连接地址": f"{self.host}:{self.port}",
            "登录用户": self.username,
            "服务来源": "systemd / service / 进程列表（按服务器环境自动选择）",
            "运行服务": self._run(client, self.SERVICES_COMMAND),
        })

    def query_pm2(self) -> Dict[str, Any]:
        return self._read(lambda client: {
            "连接地址": f"{self.host}:{self.port}",
            "登录用户": self.username,
            "PM2 状态": self._run(client, self.PM2_COMMAND),
        })

    def manage_pm2(self, action: str, name: str) -> Dict[str, Any]:
        operation = self.PM2_OPERATIONS.get(action)
        if not operation or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
            return {"ok": False, "failed": True, "message": "PM2 操作或进程名不合法。"}
        safe_name = shlex.quote(name)
        command = f"if command -v pm2 >/dev/null 2>&1; then pm2 {operation} {safe_name}; else echo '未找到 pm2 命令'; fi"
        return self._read(lambda client: {
            "连接地址": f"{self.host}:{self.port}",
            "登录用户": self.username,
            "操作": f"pm2 {operation} {name}",
            "执行结果": self._run(client, command),
        })

    def _read(self, collector: Callable[[paramiko.SSHClient], Dict[str, str]]) -> Dict[str, Any]:
        if not self.host or not self.username or not self.password:
            return {"ok": False, "failed": True, "message": "服务器 SSH 配置不完整。"}

        client = paramiko.SSHClient()
        # 原型阶段允许首次连接未知主机；正式部署时应改为校验 known_hosts。
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            return {"ok": True, "data": collector(client)}
        except paramiko.AuthenticationException:
            return {"ok": False, "failed": True, "message": "SSH 身份验证失败，请检查用户名或密码。"}
        except (paramiko.SSHException, OSError, TimeoutError) as error:
            return {"ok": False, "failed": True, "message": f"SSH 连接失败：{error}"}
        finally:
            client.close()

    @staticmethod
    def _run(client: paramiko.SSHClient, command: str) -> str:
        _, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode("utf-8", errors="replace").strip()
        error = stderr.read().decode("utf-8", errors="replace").strip()
        return output or error or "暂无数据"
