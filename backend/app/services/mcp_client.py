"""
MCP client integration.

Connects to a remote MCP server, discovers the tools it offers and exposes
them to Gemini as ordinary function declarations. The agent does not know the
difference: an MCP tool arrives in the same list as a hand-written one.

Two things stand between the two worlds and both are handled here:

- Names. MCP servers use hyphens (notion-search); Gemini function names cannot
  contain them. Names are translated on the way out and back on the way in, so
  the model never sees a name it cannot call.
- Schemas. MCP publishes full JSON Schema; Gemini accepts a narrower subset and
  rejects the whole declaration when it meets a construct it does not know.
  Schemas are reduced to that subset rather than passed through.

OAuth tokens live in the document store, so they survive a restart and follow
whichever backend is configured.
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Gemini rejects a declaration outright if the schema contains a keyword it
# does not implement, so anything outside this set is dropped.
_ALLOWED_SCHEMA_KEYS = {
    "type", "description", "properties", "required", "items",
    "enum", "nullable", "title",
}

# `format` is only honoured for a couple of values; the rest make Gemini fail.
_ALLOWED_FORMATS = {"date-time", "enum"}


def _sanitize_schema(schema: Any, depth: int = 0) -> dict:
    """
    Reduce a JSON Schema to the subset Gemini accepts.

    Unsupported constructs are dropped rather than approximated. A tool whose
    schema loses a constraint still works — the model simply is not told about
    that constraint — whereas a declaration Gemini refuses takes every other
    tool in the same request down with it.
    """
    if not isinstance(schema, dict) or depth > 12:
        return {"type": "string"}

    # A union is common for optional fields. Keep the first real branch;
    # Gemini has no way to express the union itself.
    for union_key in ("anyOf", "oneOf", "allOf"):
        if union_key in schema:
            for branch in schema[union_key]:
                if isinstance(branch, dict) and branch.get("type") != "null":
                    merged = {
                        k: v for k, v in schema.items()
                        if k not in ("anyOf", "oneOf", "allOf")
                    }
                    merged.update(branch)
                    return _sanitize_schema(merged, depth + 1)
            return {"type": "string"}

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _ALLOWED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            out["properties"] = {
                name: _sanitize_schema(sub, depth + 1) for name, sub in value.items()
            }
        elif key == "items":
            out["items"] = _sanitize_schema(value, depth + 1)
        elif key == "type" and isinstance(value, list):
            # ["string", "null"] -> string, nullable
            real = [t for t in value if t != "null"]
            out["type"] = real[0] if real else "string"
            if len(real) < len(value):
                out["nullable"] = True
        else:
            out[key] = value

    if "format" in schema and schema["format"] in _ALLOWED_FORMATS:
        out["format"] = schema["format"]

    out.setdefault("type", "object" if "properties" in out else "string")

    # Gemini rejects an object whose properties map is missing entirely.
    if out["type"] == "object" and "properties" not in out:
        out["properties"] = {}

    # A required entry naming a property that did not survive is a hard error.
    if "required" in out and isinstance(out.get("properties"), dict):
        kept = set(out["properties"])
        out["required"] = [r for r in out["required"] if r in kept]
        if not out["required"]:
            out.pop("required")

    return out


def _to_gemini_name(mcp_name: str) -> str:
    """notion-search -> notion_search."""
    cleaned = "".join(c if (c.isalnum() or c == "_") else "_" for c in mcp_name)
    if cleaned and cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned or "unnamed_tool"


def _flatten_result(result: Any) -> Any:
    """
    Turn an MCP tool result into something a model can read.

    MCP returns a list of typed content blocks. The text of those blocks is
    what matters; when a block carries JSON it is parsed so the model sees
    structure rather than a quoted string.
    """
    blocks = getattr(result, "content", None) or []
    texts: list[Any] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            texts.append(json.loads(text))
        except (ValueError, TypeError):
            texts.append(text)

    if getattr(result, "isError", False):
        return {"error": texts or "the tool reported an error with no detail"}
    if not texts:
        return {"ok": True}
    return texts[0] if len(texts) == 1 else texts


class McpIntegration:
    """
    One connection to one MCP server.

    Built but not connected: connect() is called from the app lifespan and is
    allowed to fail. A server that is unreachable at startup disables the
    integration rather than stopping the agent, which matches how every other
    optional integration behaves.
    """

    def __init__(
        self,
        name: str,
        url: str,
        token_storage: Any = None,
        headers: Optional[dict[str, str]] = None,
        tool_filter: Optional[set[str]] = None,
    ) -> None:
        self.name = name
        self._url = url
        self._storage = token_storage
        self._headers = headers or {}
        self._tool_filter = tool_filter
        self._stack: Optional[AsyncExitStack] = None
        self._session: Any = None
        self._declarations: list[dict] = []
        self._name_map: dict[str, str] = {}   # gemini name -> mcp name
        self.connected = False

    async def connect(self) -> bool:
        """Open the session and discover tools. Returns whether it worked."""
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            auth = await self._build_auth()

            self._stack = AsyncExitStack()
            read, write, _ = await self._stack.enter_async_context(
                streamablehttp_client(self._url, headers=self._headers, auth=auth)
            )
            self._session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
            await self._load_tools()
            self.connected = True
            logger.info(
                "MCP '%s' connected: %d tools", self.name, len(self._declarations)
            )
            return True
        except Exception as exc:
            logger.warning("MCP '%s' unavailable: %s", self.name, exc)
            await self.close()
            return False

    async def _build_auth(self) -> Any:
        """OAuth provider, or None when the server takes a plain header."""
        if self._storage is None:
            return None
        try:
            from mcp.client.auth import OAuthClientProvider
            from mcp.shared.auth import OAuthClientMetadata

            # Only useful once a token is already stored: a server process
            # cannot open a browser, so the interactive leg runs in the setup
            # script and this side only refreshes what it finds.
            if await self._storage.get_tokens() is None:
                logger.info(
                    "MCP '%s': no stored token, run the setup script first", self.name
                )
                return None

            return OAuthClientProvider(
                server_url=self._url,
                client_metadata=OAuthClientMetadata(
                    client_name="aileon",
                    redirect_uris=["http://localhost:8765/callback"],
                    grant_types=["authorization_code", "refresh_token"],
                    response_types=["code"],
                ),
                storage=self._storage,
                redirect_handler=_no_browser,
            )
        except Exception as exc:
            logger.warning("MCP '%s': could not build auth: %s", self.name, exc)
            return None

    async def _load_tools(self) -> None:
        listing = await self._session.list_tools()
        self._declarations = []
        self._name_map = {}

        for tool in listing.tools:
            if self._tool_filter and tool.name not in self._tool_filter:
                continue
            gemini_name = _to_gemini_name(tool.name)
            self._name_map[gemini_name] = tool.name
            self._declarations.append({
                "name": gemini_name,
                "description": (tool.description or tool.name)[:1024],
                "parameters": _sanitize_schema(tool.inputSchema or {}),
            })

    def get_tool_declarations(self) -> list[dict]:
        return list(self._declarations)

    def handles(self, gemini_name: str) -> bool:
        return gemini_name in self._name_map

    async def call(self, gemini_name: str, arguments: dict) -> Any:
        """Run a tool. Never raises: an error is a result the model can read."""
        mcp_name = self._name_map.get(gemini_name)
        if mcp_name is None or self._session is None:
            return {"error": f"unknown MCP tool: {gemini_name}"}
        try:
            result = await self._session.call_tool(mcp_name, arguments or {})
            return _flatten_result(result)
        except Exception as exc:
            logger.error("MCP '%s' call %s failed: %s", self.name, mcp_name, exc)
            return {"error": f"{mcp_name} failed: {exc}"}

    async def close(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                pass
        self._stack = None
        self._session = None
        self.connected = False


async def _no_browser(url: str) -> None:
    """Redirect handler for the server process, which cannot open a browser."""
    logger.error(
        "MCP wants interactive authorisation at %s — run the setup script", url
    )


class DatabaseTokenStorage:
    """
    MCP's TokenStorage backed by the agent's database.

    Tokens outlive the process, so a container restart does not send the user
    back through consent.
    """

    def __init__(self, db: Any, user_id: str, server: str) -> None:
        self._db = db
        self._user_id = user_id
        self._server = server

    def _read(self) -> dict:
        try:
            return self._db.query_one(
                "SELECT * FROM mcp_auth WHERE user_id = ? AND server = ?",
                (self._user_id, self._server),
            ) or {}
        except Exception:
            return {}

    def _write(self, patch: dict) -> None:
        from datetime import datetime, timezone
        try:
            self._db.upsert("mcp_auth", {
                "user_id": self._user_id,
                "server": self._server,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                **patch,
            }, keys=["user_id", "server"])
        except Exception as exc:
            logger.error("Could not persist MCP token: %s", exc)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        raw = self._read().get("tokens")
        return OAuthToken(**raw) if raw else None

    async def set_tokens(self, tokens) -> None:
        self._write({"tokens": tokens.model_dump(exclude_none=True, mode="json")})

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        raw = self._read().get("client_info")
        return OAuthClientInformationFull(**raw) if raw else None

    async def set_client_info(self, client_info) -> None:
        self._write(
            {"client_info": client_info.model_dump(exclude_none=True, mode="json")}
        )
