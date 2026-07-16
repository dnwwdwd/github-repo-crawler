#!/usr/bin/env python3
"""Search and inspect GitHub repositories without third-party dependencies."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.github.com"
USER_AGENT = "github-repo-crawler-skill/1.0"
UNKNOWN_LICENSE_VALUES = {"", "noassertion", "none", "unknown", "no license information found"}


class InputError(ValueError):
    """Raised for an invalid command input."""


@dataclass
class GitHubApiError(Exception):
    status: int
    message: str
    details: Any
    headers: dict[str, str]


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InputError(message)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def token_config() -> tuple[str, str]:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return "", ""


def request_headers(token: str, *, accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {"Accept": accept, "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def header_map(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


def parse_response_body(raw: bytes) -> Any:
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def api_request(path: str, token: str, *, params: dict[str, Any] | None = None, accept: str = "application/vnd.github+json") -> tuple[Any, dict[str, str]]:
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(url, headers=request_headers(token, accept=accept))
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 -- fixed GitHub API base
            return parse_response_body(response.read()), header_map(response.headers)
    except HTTPError as exc:
        raw = exc.read()
        details = parse_response_body(raw)
        message = details.get("message", "GitHub API request failed") if isinstance(details, dict) else "GitHub API request failed"
        raise GitHubApiError(exc.code, str(message), details, header_map(exc.headers)) from exc
    except URLError as exc:
        raise GitHubApiError(0, f"GitHub API request failed: {exc.reason}", {}, {}) from exc


def rate_limit(headers: dict[str, str]) -> dict[str, int | str | None]:
    def integer(name: str) -> int | None:
        value = headers.get(name)
        return int(value) if value and value.isdigit() else None

    return {
        "limit": integer("x-ratelimit-limit"),
        "remaining": integer("x-ratelimit-remaining"),
        "reset": integer("x-ratelimit-reset"),
        "resource": headers.get("x-ratelimit-resource"),
    }


def license_status(license_info: Any, *, source: str) -> dict[str, str]:
    info = license_info if isinstance(license_info, dict) else {}
    spdx_id = str(info.get("spdx_id") or "").strip()
    name = str(info.get("name") or "").strip()
    key = str(info.get("key") or "").strip()
    normalized = (spdx_id or name).lower()
    status = "declared" if normalized not in UNKNOWN_LICENSE_VALUES else "unknown"
    return {"status": status, "spdx_id": spdx_id, "name": name, "key": key, "source": source}


def normalize_repository(payload: dict[str, Any]) -> dict[str, Any]:
    owner = payload.get("owner") if isinstance(payload.get("owner"), dict) else {}
    return {
        "full_name": str(payload.get("full_name") or ""),
        "html_url": str(payload.get("html_url") or ""),
        "owner": str(owner.get("login") or ""),
        "name": str(payload.get("name") or ""),
        "description": str(payload.get("description") or ""),
        "language": str(payload.get("language") or ""),
        "topics": [str(topic) for topic in payload.get("topics") or []],
        "stars": int(payload.get("stargazers_count") or 0),
        "forks": int(payload.get("forks_count") or 0),
        "open_issues": int(payload.get("open_issues_count") or 0),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "pushed_at": payload.get("pushed_at"),
        "default_branch": str(payload.get("default_branch") or ""),
        "archived": bool(payload.get("archived")),
        "fork": bool(payload.get("fork")),
        "visibility": str(payload.get("visibility") or ""),
        "license": license_status(payload.get("license"), source="search"),
    }


def build_search_query(query: str, stars_min: int | None, stars_max: int | None) -> str:
    normalized = " ".join(query.split())
    if not normalized:
        raise InputError("--query must not be empty")
    if (stars_min is None) != (stars_max is None):
        raise InputError("--stars-min and --stars-max must be provided together")
    if stars_min is None:
        return normalized
    if stars_min < 0 or stars_max < 0:
        raise InputError("star bounds must be non-negative")
    if stars_min > stars_max:
        raise InputError("--stars-min must not be greater than --stars-max")
    if re.search(r"(?:^|\s)stars:", normalized, flags=re.IGNORECASE):
        raise InputError("do not combine a stars: qualifier in --query with --stars-min/--stars-max")
    return f"{normalized} stars:{stars_min}..{stars_max}"


def validate_pagination(page: int, max_pages: int, per_page: int) -> None:
    if page < 1:
        raise InputError("--page must be at least 1")
    if not 1 <= max_pages <= 10:
        raise InputError("--max-pages must be between 1 and 10")
    if not 1 <= per_page <= 100:
        raise InputError("--per-page must be between 1 and 100")
    if (page - 1 + max_pages) * per_page > 1000:
        raise InputError("GitHub Search exposes at most 1000 results; reduce page, max-pages, or per-page")


def warnings_for(token: str) -> list[str]:
    if token:
        return []
    return ["No token found. Requests are anonymous and typically limited to 60 requests per hour."]


def run_search(args: argparse.Namespace) -> dict[str, Any]:
    query = build_search_query(args.query, args.stars_min, args.stars_max)
    validate_pagination(args.page, args.max_pages, args.per_page)
    token, token_source = token_config()
    repositories: list[dict[str, Any]] = []
    pages_fetched = 0
    last_headers: dict[str, str] = {}

    for current_page in range(args.page, args.page + args.max_pages):
        payload, last_headers = api_request(
            "/search/repositories",
            token,
            params={"q": query, "sort": args.sort, "order": args.order, "per_page": args.per_page, "page": current_page},
        )
        items = payload.get("items", []) if isinstance(payload, dict) else []
        repositories.extend(normalize_repository(item) for item in items if isinstance(item, dict))
        pages_fetched += 1
        if len(items) < args.per_page:
            break

    return {
        "request": {"query": query, "sort": args.sort, "order": args.order, "page": args.page, "max_pages": args.max_pages, "per_page": args.per_page},
        "authentication": {"mode": "token" if token else "anonymous", "source": token_source or None},
        "pagination": {"pages_requested": args.max_pages, "pages_fetched": pages_fetched, "result_count": len(repositories)},
        "rate_limit": rate_limit(last_headers),
        "warnings": warnings_for(token),
        "repositories": repositories,
    }


def parse_repo(value: str) -> tuple[str, str]:
    parts = value.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise InputError("--repo must be in owner/repository form")
    return parts[0], parts[1]


def readme_payload(owner: str, repo: str, token: str, max_chars: int) -> tuple[dict[str, Any], dict[str, str]]:
    try:
        content, headers = api_request(f"/repos/{quote(owner)}/{quote(repo)}/readme", token, accept="application/vnd.github.raw+json")
    except GitHubApiError as exc:
        if exc.status == 404:
            return {"status": "missing", "content": "", "truncated": False}, exc.headers
        raise
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return {"status": "available", "content": text[:max_chars], "truncated": len(text) > max_chars}, headers


def tree_payload(owner: str, repo: str, branch: str, token: str, max_items: int) -> tuple[dict[str, Any], dict[str, str]]:
    payload, headers = api_request(f"/repos/{quote(owner)}/{quote(repo)}/git/trees/{quote(branch, safe='')}", token, params={"recursive": "1"})
    items = payload.get("tree", []) if isinstance(payload, dict) else []
    paths = [str(item.get("path")) for item in items if isinstance(item, dict) and item.get("path")]
    return {"status": "available", "paths": paths[:max_items], "truncated": bool(payload.get("truncated")) or len(paths) > max_items}, headers


def file_payload(owner: str, repo: str, path: str, token: str, max_chars: int) -> tuple[dict[str, Any], dict[str, str]]:
    normalized_path = path.strip().lstrip("/")
    if not normalized_path:
        raise InputError("--file paths must not be empty")
    payload, headers = api_request(f"/repos/{quote(owner)}/{quote(repo)}/contents/{quote(normalized_path, safe='/')}", token)
    if isinstance(payload, list):
        return {"path": normalized_path, "status": "directory", "content": "", "truncated": False}, headers
    if not isinstance(payload, dict):
        return {"path": normalized_path, "status": "unknown", "content": "", "truncated": False}, headers
    raw = str(payload.get("content") or "")
    if payload.get("encoding") == "base64":
        try:
            content = base64.b64decode(raw, validate=False).decode("utf-8", errors="replace")
        except ValueError as exc:
            raise InputError(f"could not decode {normalized_path} as base64") from exc
    else:
        content = raw
    return {
        "path": normalized_path,
        "status": "available",
        "size": int(payload.get("size") or len(content)),
        "html_url": str(payload.get("html_url") or ""),
        "content": content[:max_chars],
        "truncated": len(content) > max_chars,
    }, headers


def resolve_license(owner: str, repo: str, token: str) -> tuple[dict[str, str], dict[str, str]]:
    try:
        payload, headers = api_request(f"/repos/{quote(owner)}/{quote(repo)}/license", token)
    except GitHubApiError as exc:
        if exc.status == 404:
            return {"status": "missing", "spdx_id": "", "name": "", "key": "", "source": "license_endpoint"}, exc.headers
        raise
    info = payload.get("license") if isinstance(payload, dict) else None
    return license_status(info, source="license_endpoint"), headers


def run_inspect(args: argparse.Namespace) -> dict[str, Any]:
    if args.readme_chars < 1 or args.file_chars < 1 or args.tree_limit < 1:
        raise InputError("content limits must be at least 1")
    owner, repo = parse_repo(args.repo)
    token, token_source = token_config()
    metadata_payload, last_headers = api_request(f"/repos/{quote(owner)}/{quote(repo)}", token)
    if not isinstance(metadata_payload, dict):
        raise InputError("GitHub returned invalid repository metadata")
    metadata = normalize_repository(metadata_payload)
    license_info, last_headers = resolve_license(owner, repo, token)
    metadata["license"] = license_info
    readme, last_headers = readme_payload(owner, repo, token, args.readme_chars)
    branch = str(metadata_payload.get("default_branch") or "HEAD")
    tree, last_headers = tree_payload(owner, repo, branch, token, args.tree_limit)
    files = []
    for requested_path in args.files:
        item, last_headers = file_payload(owner, repo, requested_path, token, args.file_chars)
        files.append(item)

    return {
        "request": {"repo": f"{owner}/{repo}", "files": args.files},
        "authentication": {"mode": "token" if token else "anonymous", "source": token_source or None},
        "rate_limit": rate_limit(last_headers),
        "warnings": warnings_for(token),
        "repository": metadata,
        "readme": readme,
        "tree": tree,
        "files": files,
    }


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(description="Search and inspect GitHub repositories.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    search = subcommands.add_parser("search", help="Search repositories and return normalized JSON.")
    search.add_argument("--query", required=True, help="Required GitHub Search query.")
    search.add_argument("--stars-min", type=int, help="Optional inclusive minimum star count.")
    search.add_argument("--stars-max", type=int, help="Optional inclusive maximum star count.")
    search.add_argument("--sort", choices=("stars", "forks", "help-wanted-issues", "updated"), default="updated")
    search.add_argument("--order", choices=("asc", "desc"), default="desc")
    search.add_argument("--page", type=int, default=1)
    search.add_argument("--max-pages", type=int, default=1)
    search.add_argument("--per-page", type=int, default=30)
    search.set_defaults(handler=run_search)

    inspect = subcommands.add_parser("inspect", help="Inspect a selected repository and return evidence JSON.")
    inspect.add_argument("--repo", required=True, help="Repository in owner/repository form.")
    inspect.add_argument("--file", dest="files", action="append", default=[], help="Repository file to read; repeat as needed.")
    inspect.add_argument("--readme-chars", type=int, default=12000)
    inspect.add_argument("--tree-limit", type=int, default=500)
    inspect.add_argument("--file-chars", type=int, default=12000)
    inspect.set_defaults(handler=run_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        emit(args.handler(args))
        return 0
    except InputError as exc:
        emit({"error": {"kind": "invalid_input", "message": str(exc)}})
        return 2
    except GitHubApiError as exc:
        emit({"error": {"kind": "github_api_error", "status": exc.status, "message": exc.message, "details": exc.details}, "rate_limit": rate_limit(exc.headers)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
