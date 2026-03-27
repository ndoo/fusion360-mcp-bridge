# Fusion 360 MCP Bridge for Claude

Connects Claude to a live Fusion 360 session via two MCP tools:

- **`fusion_execute`** — run any Python script inside Fusion with full `adsk.*` API access
- **`fusion_screenshot`** — capture the active viewport as a PNG for visual verification

```
Claude  ←→  MCP Server (Python)  ←→  Fusion Add-in (HTTP)  ←→  Fusion 360 API
```

The bridge is intentionally minimal — a thin connection layer. All Fusion API
knowledge lives in [CLAUDE.md](CLAUDE.md), which Claude reads automatically at
the start of every session.

---

## Requirements

| Requirement | Notes |
|---|---|
| [Fusion 360](https://www.autodesk.com/products/fusion-360/) | Free personal licence is sufficient |
| Claude ([Code](https://claude.ai/claude-code) or [desktop](https://claude.ai/download)) | See installation options below |
| Python 3.9+ | Comes with macOS 12+; `brew install python` otherwise |

### Installing Claude

**Claude Code (CLI) — pick one:**
```bash
# Homebrew (recommended on macOS)
brew install --cask claude-code

# npm
npm install -g @anthropic-ai/claude-code

# Official installer
curl -fsSL https://claude.ai/install.sh | bash
```

> Note: Homebrew installs do not auto-update. Run `brew upgrade claude-code` periodically.

**Claude desktop app (GUI):**
Download and install from [claude.ai/download](https://claude.ai/download). MCP configuration differs slightly — see [Configure the Claude desktop app](#configure-the-claude-desktop-app) below.

---

## Quick start (macOS)

Clone the repo anywhere you like, then run the one-shot setup script:

```bash
git clone https://github.com/ndoo/fusion360-mcp-bridge.git
cd fusion360-mcp-bridge
bash scripts/quickstart-mac.sh
```

The script installs Python dependencies, generates a shared secret token,
copies the add-in into Fusion 360's add-in folder, and patches
`~/.claude/settings.json` automatically (Claude Code only — desktop app users
see [Configure the Claude desktop app](#configure-the-claude-desktop-app)).

After it finishes, follow the two manual steps it prints:

1. **In Fusion 360** — Tools → Add-Ins (`Shift+S`) → select **FusionMCPBridge** → click **Run** (check *Run on Startup* to make this permanent).
2. **Restart Claude** so the MCP server is picked up.

---

## Manual installation

### 1 — Python dependencies

**Option A — virtual environment (recommended on macOS with Homebrew Python)**

```bash
python3 -m venv ~/venv
source ~/venv/bin/activate
pip install -r mcp-server/requirements.txt
```

The quickstart script and Claude settings will automatically use `~/venv/bin/python3`
as the MCP server interpreter when that venv exists.

**Option B — user install**

```bash
pip3 install -r mcp-server/requirements.txt --user
```

### 2 — Generate shared secret

```bash
python3 -c "import secrets; print(secrets.token_hex(32))" > ~/.fusion-mcp-secret
chmod 600 ~/.fusion-mcp-secret
```

Both the add-in and MCP server read `~/.fusion-mcp-secret` at startup. The
add-in rejects any HTTP request that does not carry a matching Bearer token,
ensuring only the MCP server process (which holds the same file) can call
`/execute` and `/screenshot`.

### 3 — Fusion 360 add-in

Copy the add-in folder into Fusion's add-in directory:

**macOS**
```bash
cp -r fusion-addin/FusionMCPBridge \
  ~/Library/Application\ Support/Autodesk/Autodesk\ Fusion\ 360/API/AddIns/
```

**Windows**
```
%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\
```
Copy the `fusion-addin/FusionMCPBridge` folder there.

Then in Fusion 360:
1. **Tools → Add-Ins** (or press `Shift+S`)
2. Select **FusionMCPBridge** under *My Add-Ins*
3. Click **Run**
4. A dialog confirms the bridge started and whether token auth is enabled

To start automatically on every launch, check **Run on Startup**.

### 4 — Configure Claude

#### Claude Code (CLI)

Add the `mcpServers` block to `~/.claude/settings.json` (create the file if it doesn't exist):

```json
{
  "mcpServers": {
    "fusion360": {
      "command": "python3",
      "args": ["/ABSOLUTE/PATH/TO/fusion360-mcp-bridge/mcp-server/server.py"]
    }
  }
}
```

Replace the path with the absolute path to your clone. Restart Claude Code — the MCP server starts fresh each session.

#### Configure the Claude desktop app

Open **Claude → Settings → Developer → Edit Config** and add the same `mcpServers` block to `claude_desktop_config.json`:

**macOS** — `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows** — `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "fusion360": {
      "command": "python3",
      "args": ["/ABSOLUTE/PATH/TO/fusion360-mcp-bridge/mcp-server/server.py"]
    }
  }
}
```

Save the file and restart the Claude desktop app.

---

## Verify the connection

With Fusion open and the add-in running:

```bash
# Health check (requires secret token)
curl -H "Authorization: Bearer $(cat ~/.fusion-mcp-secret)" \
     http://localhost:7654/health
```

Or ask Claude: *"Check the Fusion 360 connection status"*

---

## Tools

| Tool | Description |
|---|---|
| `fusion_execute` | Run arbitrary Python scripts inside Fusion with full `adsk.*` API access |
| `fusion_screenshot` | Capture the active viewport as a base64 PNG |

### Example prompts

```
Create a 50 × 30 × 20 mm box centred on the origin
Make a 6 cm diameter sphere
Show me what's in the active design
Take an isometric screenshot
List all bodies and their volumes
Create a truncated icosahedron (soccer ball) 6 cm across
```

### Security

The add-in binds to `127.0.0.1` only and requires a Bearer token on every
request. The token is stored in `~/.fusion-mcp-secret` (mode 600). Any request
without a valid token receives a `401 Unauthorized` response.

If you see a "WARNING: no secret file" message when the add-in starts,
re-run `quickstart-mac.sh` or create the secret file manually (see step 2 above).

---

## Keeping the bridge up to date

The shim has two files that may need updating over time: the Fusion add-in and
`CLAUDE.md`. The add-in's API surface is intentionally minimal (execute +
screenshot), so it rarely needs changes. `CLAUDE.md` holds all the scripting
knowledge and can be updated without touching any code.

### After a Fusion update — paste into Claude

```
Read CLAUDE.md in this repo.

Then use fusion_execute to probe the running Fusion instance:
  print(adsk.core.Application.get().version)

Check whether any patterns documented in CLAUDE.md produce deprecation warnings
or errors against the current Fusion version. Report findings and update
CLAUDE.md only — no code changes needed.
```

### When Autodesk ships native MCP support — paste into Claude

```
Autodesk has shipped native MCP support for Fusion 360.

1. Read .mcp.json, mcp-server/server.py, CLAUDE.md, and README.md in this repo.
2. List which of our two tools (fusion_execute, fusion_screenshot) are now
   available natively in Autodesk's MCP, and what their tool names/schemas are.
3. For tools now provided natively:
   - Update .mcp.json to point at Autodesk's MCP server
   - Remove mcp-server/ and fusion-addin/ directories
   - Remove scripts/quickstart-mac.sh
4. Update CLAUDE.md:
   - Remove workarounds for things now handled natively
   - Add any new native tool names or patterns worth knowing
   - Keep all Fusion API knowledge (units, revolve rules, TBrepM, etc.) —
     it is still valid regardless of the transport layer
5. Update README.md to reflect the simplified setup.

Do not delete CLAUDE.md. The scripting knowledge is transport-independent.
```

---

## Thread safety

Fusion's Python API must only be called on the main thread. The add-in uses
Fusion's `CustomEvent` system to marshal every HTTP request from the background
thread onto the main thread, then blocks with a `threading.Event` until the
result is ready.

## Port configuration

Default port is **7654**. Override with an environment variable:
```bash
export FUSION_MCP_PORT=7655
```

Set this before launching Fusion 360 (so the add-in picks it up) and pass it
to the MCP server via the `args` array in your Claude config.

---

## Troubleshooting

**"Cannot connect to Fusion 360"**
- Ensure Fusion 360 is open with a design loaded
- Ensure the FusionMCPBridge add-in is running (Tools → Add-Ins)
- Check nothing else is using port 7654: `lsof -i :7654`

**"Unauthorized (401)"**
- The secret in `~/.fusion-mcp-secret` doesn't match what the add-in loaded at startup
- Re-run `quickstart-mac.sh` to regenerate a fresh shared secret, then restart both Fusion and Claude

**"No active document"**
- Open or create a Fusion design before using the tools

**Add-in won't load**
- Check Fusion's script console: Tools → Scripts and Add-Ins → Script Console
- The add-in folder must be named exactly `FusionMCPBridge` and contain both `.py` and `.manifest` files

**MCP server not found by Claude**
- Confirm the absolute path in your Claude settings file is correct
- Run `python3 mcp-server/server.py` manually to check for import errors

---

## License

MIT — see [LICENSE](LICENSE).
