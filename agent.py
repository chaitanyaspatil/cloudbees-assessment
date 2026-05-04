"""Read-only QA agent over the langchain-ai/langchain GitHub repo.

Usage:
    python agent.py "Is this project healthy?"

Requires ANTHROPIC_API_KEY in .env. GITHUB_TOKEN strongly recommended.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import AIMessage

from tools import ALL_TOOLS

# load environment variables
load_dotenv()

# I've hardcoded the repo for now but it could later become an argument
REPO = "langchain-ai/langchain"

# Using sonnet-4-6, which I have good experience with. 
# Opus would be overkill. Haiku might be a good fallback model in case we hit rate limits.
MODEL = "anthropic:claude-sonnet-4-6"

SYSTEM_PROMPT = f"""You are a read-only QA agent investigating the GitHub repository "{REPO}". \
Pass this exact string as the `repo` argument to every tool.

INVESTIGATE, DO NOT JUST ROUTE.
Form a hypothesis about where the answer might live, call a tool, READ what came back, then decide your next move \
based on what you observed. Most worthwhile questions may require multiple tools, or multiple refined queries of \
the same tool.

CITE EVERYTHING.
Every factual claim in your final answer must cite a stable identifier:
  - Issue or PR: #<number>
  - Commit: 10-char SHA
  - Release: tag (e.g. v0.3.27)
Include the source's html_url where helpful. If you cannot cite a source for a claim, do not make the claim.

TOOLS RETURN {{ok, data, error}}.
On `rate_limited` or `not_found`, do not retry the same call — adapt. \
An empty search result is information, not a dead end: try a different label, a broader query, or a different tool. \
The repo's exact label vocabulary is unknown to you; if a label-filtered search returns 0, consider calling \
list_labels to learn the taxonomy before guessing again.

BE DELIBERATE WITH CALLS.
A single run should rarely need more than ~10 tool calls. If you find yourself running similar searches in a loop, \
stop and synthesize what you already have.

ANSWER FORMAT.
Begin your final answer with one short paragraph naming the sources you \
consulted and why. Then present your findings, with citations inline."""


def _final_answer(messages) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
            return m.content if isinstance(m.content, str) else str(m.content)
    return "(no final answer produced)"


def run(question: str) -> None:
    agent = create_agent(
        model=MODEL,
        tools=ALL_TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})

    print(f"Q: {question}\n")
    print(_final_answer(result["messages"]))


def main() -> None:
    # return useful error message if args are missing
    if len(sys.argv) < 2:
        print('Usage: python agent.py "<question>"', file=sys.stderr)
        sys.exit(2)
    run(" ".join(sys.argv[1:]))


if __name__ == "__main__":
    main()
