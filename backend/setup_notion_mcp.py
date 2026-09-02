#!/usr/bin/env python3
"""
One-time authorisation for Notion's hosted MCP server.

Notion uses OAuth with dynamic client registration, so nothing has to be
registered by hand: this opens a browser, you approve, and the tokens land in
the document store where the running agent will find them.

    python setup_notion_mcp.py

Run it once per deployment. The server process refreshes the token on its own
afterwards; it never runs this flow itself, because a container has no browser.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

CALLBACK_PORT = 8765
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}/callback"

_received: dict[str, str] = {}
_done = threading.Event()


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches the single redirect Notion sends back after you approve."""

    def do_GET(self) -> None:  # noqa: N802  (name fixed by the base class)
        params = parse_qs(urlparse(self.path).query)
        _received["code"] = (params.get("code") or [""])[0]
        _received["state"] = (params.get("state") or [""])[0]
        error = (params.get("error") or [""])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = (
            "<h2>Authorisation failed</h2><p>%s</p>" % error if error
            else "<h2>Connected</h2><p>You can close this tab.</p>"
        )
        self.wfile.write(body.encode("utf-8"))
        _done.set()

    def log_message(self, *args) -> None:
        """Silence the default per-request logging."""


async def main() -> int:
    sys.path.insert(0, ".")
    from app.config import get_settings
    from app.services.mcp_client import DatabaseTokenStorage
    from app.storage.schema import Database

    settings = get_settings()
    url = settings.mcp_notion_url

    storage = DatabaseTokenStorage(
        Database(settings.sqlite_path), settings.user_id, "notion"
    )

    if await storage.get_tokens() is not None:
        print("A token is already stored. Delete it first to re-authorise.")
        return 0

    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.shared.auth import OAuthClientMetadata

    server = HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    async def open_browser(auth_url: str) -> None:
        print(f"\n  Opening your browser to approve access.")
        print(f"  If nothing opens, paste this:\n\n    {auth_url}\n")
        webbrowser.open(auth_url)

    async def wait_for_callback() -> tuple[str, str | None]:
        # Blocking wait moved off the event loop so the HTTP thread can run.
        await asyncio.get_running_loop().run_in_executor(None, _done.wait, 300)
        if not _received.get("code"):
            raise RuntimeError("No authorisation code received (timed out or denied)")
        return _received["code"], _received.get("state")

    auth = OAuthClientProvider(
        server_url=url,
        client_metadata=OAuthClientMetadata(
            client_name="aileon",
            redirect_uris=[CALLBACK_URL],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=storage,
        redirect_handler=open_browser,
        callback_handler=wait_for_callback,
    )

    print(f"  server   {url}")
    try:
        async with streamablehttp_client(url, auth=auth) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                print(f"\n  Connected. {len(tools)} tools available:")
                for tool in tools:
                    print(f"    {tool.name}")
    except Exception as exc:
        print(f"\n  Failed: {exc}")
        return 1
    finally:
        server.shutdown()

    if await storage.get_tokens() is None:
        print("\n  Connected, but no token was stored — the agent cannot reuse this.")
        return 1

    print("\n  Token stored. Now set:")
    print("    MCP_NOTION_ENABLED=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
