from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "github_repositories.py"
SPEC = importlib.util.spec_from_file_location("github_repositories", SCRIPT_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {"X-RateLimit-Limit": "60", "X-RateLimit-Remaining": "59"}

    def read(self):
        if isinstance(self.payload, bytes):
            return self.payload
        if isinstance(self.payload, str):
            return self.payload.encode()
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class GitHubRepositoriesTests(unittest.TestCase):
    def run_cli(self, args):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = module.main(args)
        return code, json.loads(buffer.getvalue())

    def test_build_query_without_stars(self):
        self.assertEqual(module.build_search_query("  language:Python  ", None, None), "language:Python")

    def test_build_query_with_stars_and_rejects_partial_range(self):
        self.assertEqual(module.build_search_query("topic:cli", 10, 50), "topic:cli stars:10..50")
        with self.assertRaises(module.InputError):
            module.build_search_query("topic:cli", 10, None)

    @patch.dict(os.environ, {"GITHUB_TOKEN": "primary", "GH_TOKEN": "fallback"}, clear=True)
    def test_github_token_has_priority(self):
        self.assertEqual(module.token_config(), ("primary", "GITHUB_TOKEN"))

    @patch.dict(os.environ, {}, clear=True)
    def test_anonymous_search_reports_warning_and_normalizes_result(self):
        payload = {
            "items": [{
                "full_name": "octo/example", "html_url": "https://github.com/octo/example",
                "owner": {"login": "octo"}, "name": "example", "description": "Example",
                "language": "Python", "topics": ["cli"], "stargazers_count": 12,
                "forks_count": 2, "open_issues_count": 1, "license": {"spdx_id": "MIT", "name": "MIT", "key": "mit"},
            }]
        }
        with patch.object(module, "urlopen", return_value=FakeResponse(payload)):
            code, result = self.run_cli(["search", "--query", "topic:cli"])
        self.assertEqual(code, 0)
        self.assertEqual(result["authentication"]["mode"], "anonymous")
        self.assertIn("typically limited to 60", result["warnings"][0])
        self.assertEqual(result["repositories"][0]["license"]["status"], "declared")

    @patch.dict(os.environ, {"GITHUB_TOKEN": "token"}, clear=True)
    def test_inspect_resolves_missing_license(self):
        metadata = {"full_name": "octo/example", "html_url": "https://github.com/octo/example", "owner": {"login": "octo"}, "name": "example", "default_branch": "main"}
        responses = [
            FakeResponse(metadata),
            HTTPError("https://api.github.com/repos/octo/example/license", 404, "missing", {}, io.BytesIO(b'{"message":"Not Found"}')),
            FakeResponse("# Example"),
            FakeResponse({"tree": [{"path": "README.md"}]}),
        ]
        with patch.object(module, "urlopen", side_effect=responses):
            code, result = self.run_cli(["inspect", "--repo", "octo/example"])
        self.assertEqual(code, 0)
        self.assertEqual(result["repository"]["license"]["status"], "missing")
        self.assertEqual(result["readme"]["content"], "# Example")

    def test_api_error_is_json(self):
        error = HTTPError("https://api.github.com/search/repositories", 403, "forbidden", {"X-RateLimit-Remaining": "0"}, io.BytesIO(b'{"message":"API rate limit exceeded"}'))
        with patch.object(module, "urlopen", side_effect=error):
            code, result = self.run_cli(["search", "--query", "topic:cli"])
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["status"], 403)
        self.assertEqual(result["rate_limit"]["remaining"], 0)


if __name__ == "__main__":
    unittest.main()
