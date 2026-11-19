"""Regression: lazy plugin_aliases import must work when launched as a script.

``python /path/MCP_Server/server.py`` puts only the MCP_Server folder on
sys.path, so ``import MCP_Server...`` is not resolvable there.
"""
import os
import subprocess
import sys
import textwrap

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER_DIR = os.path.join(REPO_ROOT, "MCP_Server")

_SCRIPT = textwrap.dedent("""
    import sys
    from unittest.mock import MagicMock

    # Script-style launch: only the MCP_Server folder is importable.
    server_dir = sys.argv[1]
    repo_root = sys.argv[2]
    sys.path[:] = [server_dir] + [
        p for p in sys.path if p and p not in (repo_root, server_dir)
    ]

    # The MCP SDK may not be installed; stub it as the other unit tests do.
    fastmcp = MagicMock()
    fastmcp.FastMCP.return_value.tool.return_value = lambda fn: fn
    sys.modules["mcp"] = MagicMock()
    sys.modules["mcp.server"] = MagicMock()
    sys.modules["mcp.server.fastmcp"] = fastmcp

    try:
        import MCP_Server  # noqa: F401
    except ImportError:
        pass
    else:
        raise SystemExit("repo root leaked onto sys.path; test is invalid")

    import server

    aliases = server._import_plugin_aliases()
    assert callable(aliases.get_categories)
    assert callable(aliases.get_alias_for_param)
    assert callable(aliases.resolve_alias)
    assert aliases.resolve_alias("Serum", "wavetable position") == "Osc A WT Pos"
    print("OK")
""")


def test_plugin_aliases_resolve_under_script_style_launch():
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT, SERVER_DIR, REPO_ROOT],
        capture_output=True,
        text=True,
        cwd=SERVER_DIR,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "OK" in result.stdout
