#!/usr/bin/env bash
# quickstart-mac.sh — one-shot setup for Fusion 360 MCP Bridge on macOS
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDIN_SRC="$REPO_DIR/fusion-addin/FusionMCPBridge"
FUSION_ADDIN_DIR="$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
SERVER_PATH="$REPO_DIR/mcp-server/server.py"
SECRET_FILE="$HOME/.fusion-mcp-secret"

echo "================================================"
echo " Fusion 360 MCP Bridge — macOS Quick Start"
echo "================================================"
echo ""

# ── 1. Python dependencies ────────────────────────────────────────────────────
echo "▶ Installing Python dependencies..."

# Prefer a venv at ~/venv if one exists, otherwise fall back to --user install.
# Using a venv keeps the MCP server fully isolated from the system Python.
VENV_DIR="$HOME/venv"
if [ -f "$VENV_DIR/bin/pip" ]; then
  PYTHON="$VENV_DIR/bin/python3"
  "$VENV_DIR/bin/pip" install -r "$REPO_DIR/mcp-server/requirements.txt" --quiet
  echo "  ✓ mcp + httpx installed into $VENV_DIR"
else
  PYTHON="python3"
  pip3 install -r "$REPO_DIR/mcp-server/requirements.txt" --user --quiet
  echo "  ✓ mcp + httpx installed (--user)"
fi
echo ""

# ── 2. Shared secret token ────────────────────────────────────────────────────
echo "▶ Setting up shared secret token..."
if [ -f "$SECRET_FILE" ]; then
  echo "  ✓ Secret already exists at $SECRET_FILE — keeping it"
else
  python3 -c "import secrets; print(secrets.token_hex(32))" > "$SECRET_FILE"
  chmod 600 "$SECRET_FILE"
  echo "  ✓ Generated new secret at $SECRET_FILE"
fi
echo ""

# ── 3. Fusion 360 add-in ─────────────────────────────────────────────────────
echo "▶ Installing Fusion 360 add-in..."

if [ ! -d "$FUSION_ADDIN_DIR" ]; then
  echo "  ✗ Fusion 360 add-in directory not found:"
  echo "    $FUSION_ADDIN_DIR"
  echo ""
  echo "  Make sure Fusion 360 has been launched at least once, then re-run this script."
  exit 1
fi

ADDIN_DEST="$FUSION_ADDIN_DIR/FusionMCPBridge"
if [ -d "$ADDIN_DEST" ]; then
  echo "  Existing add-in found — updating..."
  rm -rf "$ADDIN_DEST"
fi

cp -r "$ADDIN_SRC" "$ADDIN_DEST"
echo "  ✓ Add-in copied to:"
echo "    $ADDIN_DEST"
echo ""

# ── 4. Claude Code settings ───────────────────────────────────────────────────
echo "▶ Configuring Claude Code (~/.claude/settings.json)..."

mkdir -p "$HOME/.claude"

if [ ! -f "$CLAUDE_SETTINGS" ]; then
  cat > "$CLAUDE_SETTINGS" <<EOF
{
  "mcpServers": {
    "fusion360": {
      "command": "$PYTHON",
      "args": ["$SERVER_PATH"]
    }
  }
}
EOF
  echo "  ✓ Created $CLAUDE_SETTINGS"
else
  if grep -q '"fusion360"' "$CLAUDE_SETTINGS"; then
    echo "  ✓ fusion360 MCP server already present in settings — skipping"
  else
    python3 - "$CLAUDE_SETTINGS" "$SERVER_PATH" "$PYTHON" <<'PYEOF'
import json, sys

settings_path = sys.argv[1]
server_path   = sys.argv[2]
python        = sys.argv[3]

with open(settings_path) as f:
    settings = json.load(f)

settings.setdefault("mcpServers", {})
settings["mcpServers"]["fusion360"] = {
    "command": python,
    "args": [server_path]
}

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

print(f"  ✓ Merged fusion360 entry into {settings_path}")
PYEOF
  fi
fi
echo ""

# ── 5. Verify server is importable ────────────────────────────────────────────
echo "▶ Verifying MCP server syntax..."
if "$PYTHON" -c "import ast, sys; ast.parse(open('$SERVER_PATH').read()); print('  ✓ server.py OK')"; then
  :
else
  echo "  ✗ server.py has a syntax error — check the file and re-run."
  exit 1
fi
echo ""

# ── 6. Next steps ─────────────────────────────────────────────────────────────
echo "================================================"
echo " Setup complete! Two manual steps remain:"
echo "================================================"
echo ""
echo "  1. In Fusion 360:"
echo "       Tools → Add-Ins  (or Shift+S)"
echo "       Select 'FusionMCPBridge' under My Add-Ins"
echo "       Click Run  (the dialog should confirm 'token auth enabled')"
echo "       (Optional) Check 'Run on Startup'"
echo ""
echo "  2. Restart Claude Code"
echo "       The MCP server starts fresh each session."
echo ""
echo "  Then verify with:"
echo "       curl -H \"Authorization: Bearer \$(cat ~/.fusion-mcp-secret)\" \\"
echo "            http://localhost:7654/health"
echo ""
