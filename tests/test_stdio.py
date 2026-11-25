"""Subprocess checks for legacy MCP handshakes and MCP 2 discovery over stdio."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters
import pytest

from serper_mcp_server.enums import SerperTools


class StdioSmokeTest:
    """Exercise installed entrypoints without contacting the Serper API."""

    async def run(self, mode: str, entrypoint: str, cwd: Path) -> None:
        """Verify negotiation, tool discovery, error results, and shutdown."""

        if entrypoint == "module":
            command = sys.executable
            args = ["-m", "serper_mcp_server"]
        else:
            command = str(Path(sys.executable).parent / "serper-mcp-server")
            args = []
        parameters = StdioServerParameters(
            command=command,
            args=args,
            cwd=cwd,
            env={
                "SERPER_API_KEY": "",
                "MCP_METRICS_ENABLED": "false",
                "PYTHON_DOTENV_DISABLED": "1",
            },
        )
        with anyio.fail_after(15):
            async with Client(parameters, mode=mode, read_timeout_seconds=5) as client:
                assert client.server_info is not None
                assert client.server_info.name == "Serper"
                expected_version = "2025-11-25" if mode == "legacy" else "2026-07-28"
                assert client.protocol_version == expected_version
                assert client.server_capabilities.tools is not None
                assert client.instructions is not None
                assert "Serper" in client.instructions
                tools = (await client.list_tools()).tools
                assert {tool.name for tool in tools} == {
                    tool.value for tool in SerperTools
                }
                for tool in SerperTools:
                    if tool in (
                        SerperTools.GOOGLE_SEARCH_LENS,
                        SerperTools.WEBPAGE_SCRAPE,
                    ):
                        arguments = {"url": "https://example.com"}
                    elif tool == SerperTools.GOOGLE_SEARCH_REVIEWS:
                        arguments = {"fid": "test"}
                    else:
                        arguments = {"q": "test"}
                    result = await client.call_tool(tool.value, arguments)
                    assert result.is_error is True
                    content = result.content[0]
                    assert content.type == "text"
                    assert "SERPER_API_KEY is empty" in content.text


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("entrypoint", ["module", "console"])
def test_stdio_connection(mode: str, entrypoint: str, tmp_path: Path) -> None:
    """Both entrypoints connect using the Python environment running pytest."""

    asyncio.run(StdioSmokeTest().run(mode, entrypoint, tmp_path))
