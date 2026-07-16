---
name: github-repo-crawler
description: Search GitHub repositories and inspect selected candidates with evidence-based analysis. Use when Codex needs to discover repositories from a required GitHub Search query, optionally constrain a star range, resolve license status, read a repository's README, tree, or selected files, and report purpose, architecture, runtime signals, dependencies, risks, and unknowns.
---

# GitHub Repository Crawler

Use this skill for stateless GitHub repository discovery and inspection. Run the bundled helper with Python 3; it has no third-party dependencies and writes JSON to standard output.

## Credentials and limits

Read credentials only from `GITHUB_TOKEN`, then `GH_TOKEN`. Do not call `gh auth token` and do not print a token. Without either variable, continue anonymously and surface the returned rate-limit information. Anonymous core API requests are typically limited to 60 requests per hour; GitHub Search has a separate, lower limit.

## Discover repositories

Run `scripts/github_repositories.py search` with a required `--query`.

```bash
python scripts/github_repositories.py search \
  --query 'topic:cli language:Python' \
  --stars-min 50 --stars-max 500 \
  --sort updated --order desc --per-page 30
```

Treat `--query` as a GitHub Search query. It may contain ordinary GitHub qualifiers such as `language:`, `topic:`, or `created:`. Use `--stars-min` and `--stars-max` together when the caller wants a star range. Do not provide either flag for an unrestricted star count. If the query already contains `stars:`, do not also pass star flags.

Review `warnings`, `rate_limit`, and `pagination` before issuing more requests. Search only discovers candidates; it does not exclude repositories by license.

## Inspect selected candidates

Choose a small number of candidates from search results, then inspect each one. Inspection resolves the license through GitHub's license endpoint, obtains repository metadata, retrieves a bounded README and file tree, and reads only files requested with `--file`.

```bash
python scripts/github_repositories.py inspect \
  --repo owner/repository \
  --file package.json \
  --file docker-compose.yml
```

Do not request every file in a repository. Start with the README, tree, and the manifests or entry points that answer the question. Follow leads from those files with additional targeted `--file` requests. Treat a missing license as evidence, not as a reason to silently omit the repository. Treat API failures and truncated content as unknowns.

## Write the analysis

Return an evidence-based Markdown report for each inspected repository. Keep facts separate from inference and include this structure:

```markdown
## owner/repository

- **License:** declared, missing, or unknown; include SPDX ID or name and source.
- **Purpose:** what the repository appears to do.
- **Architecture and runtime:** main languages, components, startup or deployment signals.
- **Dependencies:** external services, packages, or platform requirements visible in evidence.
- **Risks and maturity signals:** maintenance, security, operational, or documentation concerns.
- **Evidence:** GitHub metadata plus README, tree, and file paths used.
- **Unknowns:** questions the available evidence cannot answer.
```

Do not apply product-specific filters, persist results, deduplicate, schedule work, or claim that uninspected files support a conclusion.
