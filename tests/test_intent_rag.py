import unittest

from agentdesk_core.intent_rag import infer_pm2_plan, retrieve_routing_context


class IntentRagTests(unittest.TestCase):
    def test_restart_with_name_is_extracted(self):
        self.assertEqual(infer_pm2_plan("重启 PM2 的 web-app"), {"action": "pm2_restart", "name": "web-app"})

    def test_restart_without_name_keeps_empty_slot(self):
        self.assertEqual(infer_pm2_plan("重启 PM2"), {"action": "pm2_restart", "name": None})

    def test_retrieval_returns_pm2_context(self):
        actions = [item["action"] for item in retrieve_routing_context("查询 PM2 当前运行的服务")]
        self.assertIn("pm2_info", actions)


if __name__ == "__main__":
    unittest.main()
