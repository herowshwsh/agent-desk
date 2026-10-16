import tempfile
import unittest
from pathlib import Path

from agentdesk_core.docs_search import search_backend_docs


class DocsSearchTests(unittest.TestCase):
    def test_search_returns_matching_file_and_line(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("GET /api/test/v1/tests\n", encoding="utf-8")
            result = search_backend_docs(root, "api test")
            self.assertTrue(result["ok"])
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["results"][0]["file"], "README.md")

    def test_empty_keyword_is_rejected(self):
        with self.assertRaises(ValueError):
            search_backend_docs(Path("."), " ")


if __name__ == "__main__":
    unittest.main()
