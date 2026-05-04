"""Thin GitHub REST API client.

Single responsibility: make the HTTP call and either return parsed JSON
or raise a typed exception. Knows nothing about LangChain or the agent
that consumes it. Callers decide how to surface failures (e.g. tools.py
catches these and presents them to the agent as data).
"""

from __future__ import annotations

import os
from typing import Any

import requests

GITHUB_API = "https://api.github.com"
HTTP_TIMEOUT = 20
USER_AGENT = "cloudbees-assessment-agent"


class GitHubError(Exception):
    """Base class for all GitHub API failures."""


class NotFound(GitHubError):
    """The requested resource does not exist (HTTP 404)."""


class RateLimited(GitHubError):
    """The primary or secondary rate limit was hit (HTTP 403/429)."""


class NetworkError(GitHubError):
    """Underlying transport failed (DNS, connection refused, timeout, etc.)."""


def gh_get(path: str, params: dict | None = None) -> Any:
    """GET against the GitHub REST API.

    Args:
        path: Path under https://api.github.com, e.g. "/repos/owner/name/readme".
        params: Optional query string params.

    Returns:
        Parsed JSON on HTTP 200.

    Raises:
        NotFound: HTTP 404.
        RateLimited: HTTP 403/429 with rate-limit body.
        NetworkError: requests-level failure.
        GitHubError: any other non-200 response.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(
            f"{GITHUB_API}{path}", params=params, headers=headers, timeout=HTTP_TIMEOUT
        )
    except requests.RequestException as e:
        raise NetworkError(str(e)) from e

    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 404:
        raise NotFound(path)
    if resp.status_code in (403, 429):
        body = resp.text.lower()
        if "rate limit" in body or "secondary rate" in body:
            raise RateLimited(f"{resp.status_code} on {path}")
    raise GitHubError(f"http_{resp.status_code} on {path}: {resp.text[:200]}")
