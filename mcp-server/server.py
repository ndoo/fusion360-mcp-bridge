#!/usr/bin/env python3
"""
Fusion 360 MCP Server for Claude

Minimal shim that bridges Claude to the FusionMCPBridge add-in running inside
Fusion 360 on localhost. Two tools only:

  fusion_execute   — run arbitrary Python scripts inside Fusion 360
  fusion_screenshot — capture the active viewport as a PNG

All knowledge about the Fusion API lives in CLAUDE.md, not here.
"""

import os
import json
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

# ── Configuration ─────────────────────────────────────────────────────────────
FUSION_PORT = int(os.environ.get("FUSION_MCP_PORT", "7654"))
FUSION_BASE = f"http://127.0.0.1:{FUSION_PORT}"
HTTP_TIMEOUT = 35.0  # slightly over add-in's 30s limit
SECRET_FILE = Path.home() / ".fusion-mcp-secret"


def _load_secret() -> str:
    try:
        return SECRET_FILE.read_text().strip()
    except Exception:
        return ""


_secret = _load_secret()

# ── Server setup ──────────────────────────────────────────────────────────────
mcp = FastMCP(
    "Fusion 360",
    instructions=(
        "Read and modify the active Fusion 360 design using fusion_execute to "
        "run Python scripts with full adsk.* API access. Use fusion_screenshot "
        "to verify results visually. See CLAUDE.md for Fusion API patterns and "
        "known gotchas."
    ),
)

# ── Shared HTTP client ────────────────────────────────────────────────────────
_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
    return _client


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_secret}"} if _secret else {}


async def _post(endpoint: str, payload: dict) -> dict:
    client = await _get_client()
    try:
        r = await client.post(
            f"{FUSION_BASE}{endpoint}",
            json=payload,
            headers=_auth_headers(),
        )
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        return {
            "error": (
                f"Cannot connect to Fusion 360 on port {FUSION_PORT}. "
                "Ensure Fusion 360 is open and FusionMCPBridge is running "
                "(Tools → Add-Ins → FusionMCPBridge → Run)."
            )
        }
    except httpx.TimeoutException:
        return {"error": "Request timed out — the operation may have taken too long."}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {
                "error": (
                    "Unauthorized (401). The secret in ~/.fusion-mcp-secret does not "
                    "match the add-in. Re-run quickstart-mac.sh to regenerate a shared secret."
                )
            }
        try:
            return e.response.json()
        except Exception:
            return {"error": str(e)}
    except Exception as e:
        return {"error": f"Unexpected error: {e}"}


# ═══════════════════════════════════════════════════════════════════════════════
# TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def fusion_execute(script: str) -> str:
    """Execute a Python script inside Fusion 360 with full adsk.* API access.

    The script context provides:
      adsk    — the adsk module (adsk.core, adsk.fusion, adsk.cam, …)
      app     — adsk.core.Application.get()
      ui      — app.userInterface
      design  — active Fusion design (adsk.fusion.Design), or None if no design open

    Return data via print(). Exceptions are returned as error messages with
    tracebacks so you can diagnose and iterate without restarting anything.

    The script may define a run(_context) function; if present it is called
    after module-level code — this matches Fusion's standard script convention.

    See CLAUDE.md for Fusion API patterns, unit conventions, and known gotchas.
    """
    result = await _post("/execute", {"script": script})
    if "error" in result:
        return f"Error: {result['error']}"
    return result.get("result", "(script executed with no output)")


@mcp.tool()
async def fusion_screenshot(
    direction: str = "current",
    width: int = 1024,
    height: int = 768,
) -> str:
    """Capture the active Fusion 360 viewport as a base64-encoded PNG.

    direction: camera preset — current (default), front, back, left, right,
               top, bottom, iso-top-right, iso-top-left, iso-bottom-right,
               iso-bottom-left
    width/height: output resolution in pixels (default 1024×768)

    Returns a JSON object with keys: screenshot (base64), format, width, height.
    """
    result = await _post("/screenshot", {
        "direction": direction,
        "width": width,
        "height": height,
    })
    if "error" in result:
        return f"Error: {result['error']}"
    return json.dumps(result)


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK RESOURCE
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.resource("fusion://status")
async def fusion_status() -> str:
    """Current Fusion 360 connection status and active document info."""
    client = await _get_client()
    try:
        r = await client.get(
            f"{FUSION_BASE}/health",
            headers=_auth_headers(),
            timeout=5.0,
        )
        data = r.json()
        lines = [
            f"Status: {data.get('status', 'unknown')}",
            f"Fusion version: {data.get('version', 'unknown')}",
            f"Active document: {data.get('documentName', 'none')}",
            f"Has design: {data.get('hasDesign', False)}",
            f"Bridge port: {data.get('port', FUSION_PORT)}",
        ]
        return "\n".join(lines)
    except httpx.ConnectError:
        return (
            f"Fusion 360 not reachable on port {FUSION_PORT}.\n"
            "Start Fusion 360 and enable the FusionMCPBridge add-in."
        )
    except Exception as e:
        return f"Error checking status: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run(transport="stdio")
