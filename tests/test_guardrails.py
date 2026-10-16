import unittest

from agentdesk_core.guardrails import validate_plan


class GuardrailTests(unittest.TestCase):
    def test_server_info_is_allowed(self):
        decision = validate_plan({"action": "server_info"})
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.permission, "READ_ONLY")

    def test_service_info_is_allowed(self):
        decision = validate_plan({"action": "service_info"})
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.permission, "READ_ONLY")

    def test_pm2_info_is_allowed(self):
        decision = validate_plan({"action": "pm2_info"})
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.permission, "READ_ONLY")

    def test_pm2_restart_requires_confirmation(self):
        decision = validate_plan({"action": "pm2_restart", "name": "web-app"})
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.permission, "REQUIRES_CONFIRMATION")

    def test_pm2_command_name_is_validated(self):
        decision = validate_plan({"action": "pm2_restart", "name": "web app; rm -rf /"})
        self.assertFalse(decision.allowed)

    def test_pm2_action_without_name_needs_input(self):
        decision = validate_plan({"action": "pm2_restart", "name": None})
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.permission, "NEEDS_INPUT")

    def test_old_backend_action_is_blocked(self):
        decision = validate_plan({"action": "list_tests"})
        self.assertFalse(decision.allowed)

    def test_extra_fields_are_blocked(self):
        decision = validate_plan({"action": "server_info", "command": "uname"})
        self.assertFalse(decision.allowed)

    def test_unknown_tool_is_blocked(self):
        decision = validate_plan({"action": "run_shell", "command": "rm -rf"})
        self.assertFalse(decision.allowed)


if __name__ == "__main__":
    unittest.main()
