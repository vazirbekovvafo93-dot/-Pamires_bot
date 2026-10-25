"""
Shared helper for the script-based runners (run_gpt.py, run_grok.py, run_any.py).

You don't run this file directly. It holds what all runners share: launching the
MCP server(s) and the trading instructions — so each runner stays tiny.

Optional: set ENABLE_TRADINGVIEW=1 to also launch a separate TradingView MCP
server alongside ours, giving the model extra data for NON-OTC pairs. Needs `uv`
installed (https://astral.sh/uv); no TradingView account required.
"""
from __future__ import annotations

import contextlib
import os
import re
import sys


# The operating manual lives in agents/ (with a legacy fallback to the project root)
# and is the single source of truth for the agent's rules — used as the system prompt.
# Each model can have its own file, agents/agent_instructions_<agent>.md; everyone else
# uses the shared agents/agent_instructions.md.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FALLBACK_INSTRUCTIONS = (
    "You are an autonomous trading agent on PocketOption (binary options) using MCP "
    "tools. Demo by default. Expiration is any number of seconds the asset allows; "
    "you can pull candles at multiple timeframes and compute your own indicators. "
    "OTC pairs use synthetic prices (no outside data); real pairs can use TradingView "
    "tools if present. Check the asset is open and the expiration allowed before "
    "place_trade, then check_result. Be conservative; binary options are negative-EV."
)


def _resolve_manual_path(agent: str) -> str | None:
    """First existing manual: per-model in agents/, shared in agents/, then legacy root."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", (agent or "").strip()).strip("_").lower() or "default"
    for candidate in (
        os.path.join(_PROJECT_ROOT, "agent_manuals", f"agent_instructions_{slug}.md"),
        os.path.join(_PROJECT_ROOT, "agent_manuals", "agent_instructions.md"),
        os.path.join(_PROJECT_ROOT, "agent_instructions.md"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def load_instructions() -> str:
    """Read the operating manual for this agent (per-model, else shared), or a fallback."""
    agent = os.environ.get("AGENT_NAME", "agent")
    path = _resolve_manual_path(agent)
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            pass
    print(
        f"(warning: no operating manual found for agent {agent!r} under agents/ or root; "
        "using the short built-in fallback prompt)",
        file=sys.stderr,
    )
    return _FALLBACK_INSTRUCTIONS


def build_pocketoption_server() -> MCPServerStdio:
    """Spawn our PocketOption MCP server as a subprocess (same one Claude uses)."""
    # project root = the folder that contains the cmp_server_pocket_option_2026 package
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Inherit the full parent environment. A minimal {"PATH": ...} dict *replaces*
    # (not merges with) the child's environment, which on Windows drops essentials
    # like SystemRoot/SYSTEMROOT — and without those, the child's TLS/socket stack
    # (used to reach PocketOption) can fail to initialise. os.environ already holds
    # PO_SSID / PO_ALLOW_REAL / AGENT_NAME etc., so they're forwarded automatically.
    env = os.environ.copy()

    # `check_result` blocks until a trade closes — that can be minutes (or, at the
    # extreme, hours for long options). If a single tool call outlives the MCP
    # session read timeout, the call fails mid-trade. So the timeout must cover the
    # longest expiration a bot might use, not the SDK's 5s default. Override with
    # PO_MCP_SESSION_TIMEOUT if you trade very long options.
    session_timeout = float(os.environ.get("PO_MCP_SESSION_TIMEOUT", "3600"))
    return MCPServerStdio(
        params={
            "command": sys.executable,
            "args": ["-m", "cmp_server_pocket_option_2026.mcp_server"],
            "cwd": project_root,
            "env": env,
        },
        cache_tools_list=True,
        client_session_timeout_seconds=session_timeout,
    )


def build_tradingview_server() -> MCPServerStdio:
    """Spawn a separate, third-party TradingView data MCP server (needs `uv`).

    Uses the published PyPI package (``tradingview-mcp-server``, entry point
    ``tradingview-mcp``) via ``uv tool run`` — the upstream's current primary
    recommendation. It's faster and versioned versus rebuilding from git on every
    launch.

    The **first** launch cold-installs the package into a fresh venv, which the
    upstream documents can take longer than 60s and otherwise surfaces as
    ``MCP error -32001: Request timed out``. Hence the generous startup timeout;
    override with ``PO_TV_SESSION_TIMEOUT`` if your first install is slower.
    (Note: the upstream server does not yet support Python 3.14.)
    """
    tv_timeout = float(os.environ.get("PO_TV_SESSION_TIMEOUT", "180"))
    return MCPServerStdio(
        params={
            "command": "uv",
            "args": [
                "tool", "run",
                "--from", "tradingview-mcp-server",
                "tradingview-mcp",
            ],
            # Full env for the same cross-platform reason as above (uv needs PATH,
            # and Windows needs SystemRoot for its network stack).
            "env": os.environ.copy(),
        },
        cache_tools_list=True,
        client_session_timeout_seconds=tv_timeout,
    )


async def run_repl(model) -> None:
    """Connect the MCP server(s) to `model` and start a simple chat loop."""
    if not os.environ.get("PO_SSID"):
        raise SystemExit("Set PO_SSID to your PocketOption SSID first.")

    async with contextlib.AsyncExitStack() as stack:
        servers = [await stack.enter_async_context(build_pocketoption_server())]

        # Optional extra data source for non-OTC pairs.
        if os.environ.get("ENABLE_TRADINGVIEW") == "1":
            try:
                servers.append(await stack.enter_async_context(build_tradingview_server()))
                print("TradingView data server connected.")
            except Exception as exc:
                print(f"(TradingView server didn't start — continuing without it: {exc})")

        agent = Agent(
            name="PocketOption Trader",
            instructions=load_instructions(),
            mcp_servers=servers,
            model=model,
        )
        print("Connected. Ask me anything (e.g. 'show my balance and top OTC "
              "pairs'). Type 'exit' to quit.\n")

        history: list = []
        while True:
            try:
                user = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user.lower() in {"exit", "quit"}:
                break
            if not user:
                continue
            history.append({"role": "user", "content": user})
            result = await Runner.run(agent, history, max_turns=20)
            print(f"\n{result.final_output}\n")
            history = result.to_input_list()
