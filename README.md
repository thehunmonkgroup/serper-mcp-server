# Serper MCP Server

[![PyPI version](https://badge.fury.io/py/serper-mcp-server.svg)](https://badge.fury.io/py/serper-mcp-server)
[![PyPI Downloads](https://static.pepy.tech/badge/serper-mcp-server)](https://pepy.tech/project/serper-mcp-server)
[![Monthly Downloads](https://static.pepy.tech/badge/serper-mcp-server/month)](https://pepy.tech/project/serper-mcp-server)
[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A Model Context Protocol server that provides **Google Search via Serper**. This server enables LLMs to get search result information from Google.

## Available Tools

- `google_search` - Set [all the parameters](src/serper_mcp_server/schemas.py#L15)
- `google_search_images` - Set [all the parameters](src/serper_mcp_server/schemas.py#L15)
- `google_search_videos` - Set [all the parameters](src/serper_mcp_server/schemas.py#L15)
- `google_search_places` - Set [all the parameters](src/serper_mcp_server/schemas.py#L20)
- `google_search_maps` - Set [all the parameters](src/serper_mcp_server/schemas.py#L24)
- `google_search_reviews` - Set [all the parameters](src/serper_mcp_server/schemas.py#L34)
- `google_search_news` - Set [all the parameters](src/serper_mcp_server/schemas.py#L15)
- `google_search_shopping` - Set [all the parameters](src/serper_mcp_server/schemas.py#L45)
- `google_search_lens` - Set [all the parameters](src/serper_mcp_server/schemas.py#L50)
- `google_search_scholar` - Set [all the parameters](src/serper_mcp_server/schemas.py#L20)
- `google_search_patents` - Set [all the parameters](src/serper_mcp_server/schemas.py#L56)
- `google_search_autocomplete` - Set [all the parameters](src/serper_mcp_server/schemas.py#L20)
- `webpage_scrape` - Set [all the parameters](src/serper_mcp_server/schemas.py#L62)

## Configuration

Set `SERPER_API_KEY` to your Serper API key.

Any tool parameter can be forced with a `SERPER_FORCE_` environment variable. Forced values take precedence over values passed by the MCP client. Parameter names are converted to upper snake case, so `includeMarkdown` becomes `SERPER_FORCE_INCLUDE_MARKDOWN`, `nextPageToken` becomes `SERPER_FORCE_NEXT_PAGE_TOKEN`, and `gl` becomes `SERPER_FORCE_GL`.

For example:

```json
{
    "SERPER_API_KEY": "<Your Serper API key>",
    "SERPER_FORCE_GL": "us",
    "SERPER_FORCE_HL": "en",
    "SERPER_FORCE_INCLUDE_MARKDOWN": "true"
}
```

### Metrics Sidecar

The server records portable SQLite metrics for search and scrape requests and
starts a local HTTP sidecar when one is not already running. The default
metrics endpoint is `http://127.0.0.1:3005`.

The sidecar exposes:

- `GET /health` - Identify the portable MCP metrics sidecar.
- `GET /metrics?scope=current` - Report the current local-date run.
- `GET /metrics?scope=all_time` - Report all recorded runs.
- `GET /metrics?scope=run&run_id=1` - Report one run by ID.

Metrics use one run per local `YYYY-MM-DD` date. Queries and URLs are stored
only as SHA-256 hashes.

Metrics configuration:

- `MCP_METRICS_ENABLED` - Enable metrics. Defaults to `true`.
- `MCP_METRICS_HOST` - Sidecar bind host. Defaults to `127.0.0.1`.
- `MCP_METRICS_PORT` - Sidecar port. Defaults to `3005`.
- `MCP_METRICS_DATA_DIR` - Directory for the default SQLite database.
  Defaults to `data`.
- `MCP_METRICS_DB_PATH` - Explicit SQLite database path. When set, this takes
  precedence over `MCP_METRICS_DATA_DIR`.

## Usage

### Installing via Smithery

To install Serper MCP Server for Claude Desktop automatically via [Smithery](https://smithery.ai/server/@garylab/serper-mcp-server):

```bash
npx -y @smithery/cli install @garylab/serper-mcp-server --client claude
```

### Using `pip`

1. Install the package.
    ```bash
    python3 -m pip install serper-mcp-server
    ```

2. In your MCP client code configuration or **Claude** settings (file `claude_desktop_config.json`), add the `serper` MCP server:
    ```json
    {
        "mcpServers": {
            "serper": {
                "command": "python3",
                "args": ["-m", "serper_mcp_server"],
                "env": {
                    "SERPER_API_KEY": "<Your Serper API key>"
                }
            }
        }
    }
    ```


### Developing locally

1. Clone the repository and install it in editable mode.
    ```bash
    git clone https://github.com/garylab/serper-mcp-server.git
    cd serper-mcp-server
    python3 -m pip install -e ".[dev]"
    ```

2. Configure your MCP client to run the local package:
    ```json
    {
        "mcpServers": {
            "serper": {
                "command": "python3",
                "args": ["-m", "serper_mcp_server"],
                "env": {
                    "SERPER_API_KEY": "<Your Serper API key>"
                }
            }
        }
    }
    ```


## Debugging

You can use the MCP inspector to debug the server after installing it with `pip`:

```bash
SERPER_API_KEY=<the key> npx @modelcontextprotocol/inspector python3 -m serper_mcp_server
```


## License

serper-mcp-server is licensed under the MIT License. This means you are free to use, modify, and distribute the software, subject to the terms and conditions of the MIT License. For more details, please see the LICENSE file in the project repository.
