"""
Tool schemas in Gemini function-calling format
(https://ai.google.dev/gemini-api/docs/function-calling).
These are the LLM-facing definitions — `state` is injected by tool_runner
and intentionally NOT exposed here.
"""

TOOL_SCHEMAS = [
    {
        "name": "search_codebase",
        "description": (
            "Semantic search over the indexed codebase. Use this to find code related to "
            "the change under review — similar functions, existing patterns, callers of a "
            "modified function, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language or code-like description of what to search for."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 5)."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "fetch_file",
        "description": (
            "Fetch the full current contents of a file from the repository by path. "
            "Use this when search_codebase only returns a partial chunk and you need full context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative file path, e.g. 'services/embedder.py'."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "find_related_files",
        "description": (
            "Find files that import the given file, or that the given file imports. "
            "Use this to understand blast radius — what else might break from this change."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Repo-relative file path to find relations for."
                }
            },
            "required": ["filename"]
        }
    },
    {
        "name": "search_docs",
        "description": (
            "Semantic search restricted to documentation files (README, docs/, *.md, *.rst). "
            "Use this to check if a change contradicts documented behavior or conventions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look up in the docs."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 5)."
                }
            },
            "required": ["query"]
        }
    }
]