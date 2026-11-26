"""Launch the pd-chatbot MCP server over stdio.

    /path/to/pd-chatbot/venv/bin/python /path/to/pd-chatbot/mcp_server.py

MCP clients start servers from their own working directory, so this moves to the
repo root first: the retrieval index (``./chroma_db``), the chunk files, the
skills directory and ``.env`` are all resolved from there. See agent/mcp_server.py.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
