"""LangChain tools the agent can call.

Each tool wraps a GitHub endpoint via github_api.gh_get and trims the
response to fields the agent actually needs. Full GitHub payloads are
noisy and burn context tokens.

On success a tool returns a plain data dict (the trimmed fields).
On failure it returns {"error": "<short code or message>"} so the agent
can observe and adapt without exceptions crossing the tool boundary.

Tool docstrings deliberately describe what each tool returns and how to
call it — NOT which question shape it should be used for. Routing is the
agent's job; documentation that pre-routes undermines the "investigate,
not route" framing.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool

from github_api import (
    GitHubError,
    NetworkError,
    NotFound,
    RateLimited,
    gh_get,
)

# Length caps to keep tool outputs from blowing context.
README_CHAR_CAP = 5000
ISSUE_BODY_CHAR_CAP = 1500
COMMENT_BODY_CHAR_CAP = 500
RELEASE_BODY_CHAR_CAP = 1500
ITEM_BODY_EXCERPT_CHAR_CAP = 300
MAX_COMMENTS_PER_ISSUE = 10
MAX_TIMELINE_EVENTS = 20


def _truncate(text: str | None, cap: int) -> str:
    if not text:
        return ""
    if len(text) <= cap:
        return text
    return text[:cap] + f"... [truncated, {len(text) - cap} chars omitted]"


def _as_error(e: GitHubError) -> dict:
    if isinstance(e, NotFound):
        return {"error": "not_found"}
    if isinstance(e, RateLimited):
        return {"error": "rate_limited"}
    if isinstance(e, NetworkError):
        return {"error": f"network_error: {e}"}
    return {"error": str(e)}


# ---------- Tool 1: read_readme ----------

@tool
def read_readme(repo: str) -> dict:
    """Fetch the README of a GitHub repo.

    Args:
        repo: GitHub repo in "owner/name" form, e.g. "langchain-ai/langchain".

    Returns:
        On success: {content, html_url}. content is truncated to ~5000 chars.
        On failure: {error}.
    """
    try:
        payload = gh_get(f"/repos/{repo}/readme")
    except GitHubError as e:
        return _as_error(e)
    encoded = payload.get("content", "")
    try:
        decoded = base64.b64decode(encoded).decode("utf-8", errors="replace")
    except Exception as e:
        return {"error": f"decode_error: {e}"}
    return {
        "content": _truncate(decoded, README_CHAR_CAP),
        "html_url": payload.get("html_url"),
    }


# ---------- Tool 2: recent_commits ----------

@tool
def recent_commits(repo: str, days: int = 14) -> dict:
    """List commits to the default branch within the last N days.

    Returns up to 30 commits.

    Args:
        repo: GitHub repo in "owner/name" form.
        days: Look-back window in days. Default 14.

    Returns:
        On success: {count, commits: [{sha, date, author, message_first_line, html_url}]}.
        On failure: {error}.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        data = gh_get(f"/repos/{repo}/commits", params={"since": since, "per_page": 30})
    except GitHubError as e:
        return _as_error(e)
    commits = []
    for c in data:
        commit = c.get("commit", {})
        msg = commit.get("message", "")
        commits.append({
            "sha": c.get("sha", "")[:10],
            "date": commit.get("author", {}).get("date"),
            "author": commit.get("author", {}).get("name"),
            "message_first_line": msg.split("\n", 1)[0][:200],
            "html_url": c.get("html_url"),
        })
    return {"count": len(commits), "commits": commits}


# ---------- Tool 3: list_releases ----------

@tool
def list_releases(repo: str, n: int = 5) -> dict:
    """List the most recent N releases.

    Args:
        repo: GitHub repo in "owner/name" form.
        n: Max releases to return. Default 5.

    Returns:
        On success: {count, releases: [{tag_name, name, published_at, body_excerpt, html_url}]}.
        On failure: {error}.
    """
    try:
        data = gh_get(f"/repos/{repo}/releases", params={"per_page": n})
    except GitHubError as e:
        return _as_error(e)
    releases = [{
        "tag_name": r.get("tag_name"),
        "name": r.get("name"),
        "published_at": r.get("published_at"),
        "body_excerpt": _truncate(r.get("body"), RELEASE_BODY_CHAR_CAP),
        "html_url": r.get("html_url"),
    } for r in data]
    return {"count": len(releases), "releases": releases}


# ---------- Tool 4: search_issues ----------

@tool
def search_issues(
    repo: str,
    query: str,
    state: str = "open",
    sort: str = "updated",
    limit: int = 15,
) -> dict:
    """Search issues in a repo. Returns total_count and a trimmed item list.

    The query is the q-syntax for GitHub issue search, MINUS the repo qualifier
    (which is added automatically). Q-syntax examples:
      - 'streaming label:"partner: anthropic"'
      - 'label:"good first issue"'
      - 'sort:comments-desc'

    Use total_count for cheap counts (no need for a separate count tool).
    Set limit=1 if you only need the count.

    Args:
        repo: GitHub repo in "owner/name" form.
        query: Issue search query (without repo: qualifier).
        state: 'open' (default), 'closed', or 'all'. Folded into query as is:state.
        sort: 'updated' (default), 'created', 'comments', or 'reactions'.
        limit: Max items to return. Default 15.

    Returns:
        On success: {total_count, items: [...]}.
        On failure: {error}.
    """
    q_parts = [f"repo:{repo}", "is:issue"]
    if state in ("open", "closed"):
        q_parts.append(f"is:{state}")
    q_parts.append(query)
    full_query = " ".join(q_parts)

    try:
        payload = gh_get("/search/issues", params={
            "q": full_query, "sort": sort, "order": "desc", "per_page": limit,
        })
    except GitHubError as e:
        return _as_error(e)
    items = []
    for it in payload.get("items", []):
        items.append({
            "number": it.get("number"),
            "title": it.get("title"),
            "state": it.get("state"),
            "labels": [l.get("name") for l in it.get("labels", [])],
            "comments": it.get("comments"),
            "created_at": it.get("created_at"),
            "updated_at": it.get("updated_at"),
            "html_url": it.get("html_url"),
            "body_excerpt": _truncate(it.get("body"), ITEM_BODY_EXCERPT_CHAR_CAP),
        })
    return {"total_count": payload.get("total_count", 0), "items": items}


# ---------- Tool 5: get_issue ----------

@tool
def get_issue(repo: str, number: int) -> dict:
    """Fetch a single issue with its body, comments, and timeline events.

    Args:
        repo: GitHub repo in "owner/name" form.
        number: Issue number.

    Returns:
        On success: {number, title, state, labels, body, assignees,
                     comments_count, comments: [...], timeline_events: [...], html_url}.
            Comments capped at 10. Timeline filtered to assigned/unassigned/
            cross-referenced/referenced/closed events; capped at 20.
        On failure: {error}.
    """
    try:
        issue = gh_get(f"/repos/{repo}/issues/{number}")
    except GitHubError as e:
        return _as_error(e)

    comments = []
    try:
        for c in gh_get(
            f"/repos/{repo}/issues/{number}/comments",
            params={"per_page": MAX_COMMENTS_PER_ISSUE},
        ):
            comments.append({
                "user": c.get("user", {}).get("login"),
                "created_at": c.get("created_at"),
                "body_excerpt": _truncate(c.get("body"), COMMENT_BODY_CHAR_CAP),
            })
    except GitHubError:
        pass  # comments are nice-to-have; don't fail the whole tool

    relevant_events = {"assigned", "unassigned", "cross-referenced", "referenced", "closed"}
    timeline = []
    try:
        for ev in gh_get(
            f"/repos/{repo}/issues/{number}/timeline",
            params={"per_page": MAX_TIMELINE_EVENTS},
        ):
            ev_type = ev.get("event")
            if ev_type not in relevant_events:
                continue
            entry = {"event": ev_type, "created_at": ev.get("created_at")}
            if ev_type in ("assigned", "unassigned"):
                entry["assignee"] = (ev.get("assignee") or {}).get("login")
            if ev_type == "cross-referenced":
                src = ev.get("source", {}).get("issue", {})
                entry["from_issue_or_pr"] = src.get("number")
                entry["from_html_url"] = src.get("html_url")
                entry["is_pull_request"] = "pull_request" in src
            timeline.append(entry)
    except GitHubError:
        pass

    return {
        "number": issue.get("number"),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "labels": [l.get("name") for l in issue.get("labels", [])],
        "assignees": [a.get("login") for a in issue.get("assignees", [])],
        "comments_count": issue.get("comments"),
        "body": _truncate(issue.get("body"), ISSUE_BODY_CHAR_CAP),
        "comments": comments,
        "timeline_events": timeline,
        "html_url": issue.get("html_url"),
    }


# ---------- Tool 6: list_labels ----------

@tool
def list_labels(repo: str) -> dict:
    """List all labels defined on the repo.

    Each label includes its name, description, and color. Does NOT include
    per-label issue counts — call search_issues with a label filter
    (limit=1) to get a count.

    Args:
        repo: GitHub repo in "owner/name" form.

    Returns:
        On success: {count, labels: [{name, description, color}]}.
        On failure: {error}.
    """
    try:
        data = gh_get(f"/repos/{repo}/labels", params={"per_page": 100})
    except GitHubError as e:
        return _as_error(e)
    labels = [{
        "name": l.get("name"),
        "description": l.get("description"),
        "color": l.get("color"),
    } for l in data]
    return {"count": len(labels), "labels": labels}


# Convenience: the list of all tools, for agent.py to import.
ALL_TOOLS = [
    read_readme,
    recent_commits,
    list_releases,
    search_issues,
    get_issue,
    list_labels,
]
