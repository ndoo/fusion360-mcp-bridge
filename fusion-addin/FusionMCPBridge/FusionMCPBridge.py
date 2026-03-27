"""
FusionMCPBridge — Fusion 360 Add-In
Minimal shim: exposes /execute (arbitrary Python) and /screenshot (viewport
capture) to Claude via a localhost HTTP server.

Security: all requests must carry a Bearer token matching ~/.fusion-mcp-secret.
The token is generated once by quickstart-mac.sh and shared with the MCP server.
The server binds to 127.0.0.1 only; the token ensures only our MCP server
process (which holds the same file) can call the endpoints.

Architecture:
  Background HTTP thread  →  fires custom event  →  Fusion main thread
  Fusion main thread      →  sets threading.Event  →  HTTP thread unblocks
  HTTP thread             →  sends JSON response back to MCP server
"""

import adsk.core
import adsk.fusion
import traceback
import threading
import json
import uuid
import sys
import io
import os
import base64
import tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────
PORT = int(os.environ.get("FUSION_MCP_PORT", "7654"))
CUSTOM_EVENT_ID = "FusionMCPBridgeEvent"
REQUEST_TIMEOUT = 30  # seconds
SECRET_FILE = Path.home() / ".fusion-mcp-secret"

# ── Shared state ──────────────────────────────────────────────────────────────
_pending_results: dict = {}
_pending_events: dict = {}
_app: adsk.core.Application = None
_ui: adsk.core.UserInterface = None
_handlers = []          # keep references so GC doesn't collect them
_http_server: HTTPServer = None
_http_thread: threading.Thread = None
_secret: str = ""       # loaded at startup; empty = auth disabled (warn only)


def _load_secret() -> str:
    """Read shared secret from ~/.fusion-mcp-secret. Returns '' if not found."""
    try:
        return SECRET_FILE.read_text().strip()
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP SERVER  (background thread)
# ═══════════════════════════════════════════════════════════════════════════════

class MCPRequestHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # suppress default access log noise

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw)

    def _send_json(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        """Return True if the request carries the correct Bearer token."""
        if not _secret:
            return True  # no secret file — open (warn logged at startup)
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {_secret}"

    def _dispatch(self, path: str, body: dict) -> tuple[int, dict]:
        """Send work to Fusion main thread, wait for result."""
        req_id = str(uuid.uuid4())
        event = threading.Event()
        _pending_events[req_id] = event

        envelope = {"id": req_id, "path": path, "body": body}
        try:
            _app.fireCustomEvent(CUSTOM_EVENT_ID, json.dumps(envelope))
        except Exception as e:
            _pending_events.pop(req_id, None)
            return 503, {"error": f"Failed to fire custom event: {e}"}

        if not event.wait(timeout=REQUEST_TIMEOUT):
            _pending_events.pop(req_id, None)
            return 504, {"error": "Fusion did not respond within timeout"}

        result = _pending_results.pop(req_id, {"error": "No result"})
        status = 500 if "error" in result else 200
        return status, result

    def do_GET(self):
        if self.path == "/health":
            if not self._authorized():
                self._send_json(401, {"error": "Unauthorized"})
                return
            status, body = self._dispatch("/health", {})
            self._send_json(status, body)
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        if not self._authorized():
            self._send_json(401, {"error": "Unauthorized"})
            return

        try:
            body = self._read_body()
        except Exception as e:
            self._send_json(400, {"error": f"Invalid JSON: {e}"})
            return

        if self.path in ("/execute", "/screenshot"):
            status, result = self._dispatch(self.path, body)
            self._send_json(status, result)
        else:
            self._send_json(404, {"error": "Not found"})


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOM EVENT HANDLER  (Fusion main thread)
# ═══════════════════════════════════════════════════════════════════════════════

class MCPEventHandler(adsk.core.CustomEventHandler):
    """Receives requests on Fusion's main thread, calls the API, stores results."""

    def notify(self, args: adsk.core.CustomEventArgs):
        envelope = None
        try:
            envelope = json.loads(args.additionalInfo)
            req_id = envelope["id"]
            path = envelope["path"]
            body = envelope["body"]

            try:
                result = self._route(path, body)
            except Exception as e:
                result = {"error": str(e), "traceback": traceback.format_exc()}

            _pending_results[req_id] = result
        except Exception:
            pass
        finally:
            req_id = envelope.get("id") if envelope else None
            if req_id and req_id in _pending_events:
                _pending_events[req_id].set()

    def _route(self, path: str, body: dict) -> dict:
        app = adsk.core.Application.get()
        if path == "/health":
            return _handle_health(app)
        if path == "/execute":
            return _handle_execute(app, body)
        if path == "/screenshot":
            return _handle_screenshot(app, body)
        return {"error": f"Unknown path: {path}"}


# ═══════════════════════════════════════════════════════════════════════════════
# /health
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_health(app: adsk.core.Application) -> dict:
    try:
        doc = app.activeDocument
        if doc:
            design = adsk.fusion.Design.cast(
                doc.products.itemByProductType("DesignProductType")
            )
            has_design = design is not None
            doc_name = doc.name
        else:
            has_design = False
            doc_name = None
        return {
            "status": "ok",
            "version": app.version,
            "hasDocument": doc is not None,
            "hasDesign": has_design,
            "documentName": doc_name,
            "port": PORT,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# /execute
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_execute(app: adsk.core.Application, body: dict) -> dict:
    script = body.get("script", "")
    if not script:
        return {"result": "Error: script is required"}

    # Resolve design (None if no design open — scripts can handle this)
    design = None
    try:
        doc = app.activeDocument
        if doc:
            design = adsk.fusion.Design.cast(
                doc.products.itemByProductType("DesignProductType")
            )
    except Exception:
        pass

    old_stdout = sys.stdout
    captured = io.StringIO()
    sys.stdout = captured

    try:
        local_ns = {
            "adsk": adsk,
            "app": app,
            "ui": app.userInterface,
            "design": design,
        }
        exec(compile(script, "<mcp_script>", "exec"), local_ns)

        # Call run() if defined — matches Fusion's standard script convention
        if "run" in local_ns and callable(local_ns["run"]):
            local_ns["run"](None)

        return {"result": captured.getvalue()}

    except Exception as e:
        out = captured.getvalue()
        msg = f"Error: {e}\n\nTraceback:\n{traceback.format_exc()}"
        if out:
            msg += f"\n\nOutput before error:\n{out}"
        return {"result": msg}

    finally:
        sys.stdout = old_stdout


# ═══════════════════════════════════════════════════════════════════════════════
# /screenshot
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_screenshot(app: adsk.core.Application, body: dict) -> dict:
    direction = body.get("direction", "current")
    width = body.get("width", 1024)
    height = body.get("height", 768)

    vp = app.activeViewport
    if not vp:
        return {"error": "No active viewport"}

    if direction != "current":
        cam = vp.camera
        _set_camera_direction(cam, direction)
        vp.camera = cam
        vp.refresh()

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        ok = vp.saveAsImageFileWithOptions(tmp.name, width, height, True)
        if not ok:
            return {"error": "Failed to save screenshot"}
        with open(tmp.name, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return {"screenshot": data, "format": "png", "width": width, "height": height}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def _set_camera_direction(cam, direction: str):
    dirs = {
        "front":            (adsk.core.Vector3D.create( 0, -1,  0), adsk.core.Vector3D.create(0, 0, 1)),
        "back":             (adsk.core.Vector3D.create( 0,  1,  0), adsk.core.Vector3D.create(0, 0, 1)),
        "left":             (adsk.core.Vector3D.create(-1,  0,  0), adsk.core.Vector3D.create(0, 0, 1)),
        "right":            (adsk.core.Vector3D.create( 1,  0,  0), adsk.core.Vector3D.create(0, 0, 1)),
        "top":              (adsk.core.Vector3D.create( 0,  0,  1), adsk.core.Vector3D.create(0, 1, 0)),
        "bottom":           (adsk.core.Vector3D.create( 0,  0, -1), adsk.core.Vector3D.create(0, 1, 0)),
        "iso-top-right":    (adsk.core.Vector3D.create( 1, -1,  1), adsk.core.Vector3D.create(0, 0, 1)),
        "iso-top-left":     (adsk.core.Vector3D.create(-1, -1,  1), adsk.core.Vector3D.create(0, 0, 1)),
        "iso-bottom-right": (adsk.core.Vector3D.create( 1,  1, -1), adsk.core.Vector3D.create(0, 0, 1)),
        "iso-bottom-left":  (adsk.core.Vector3D.create(-1,  1, -1), adsk.core.Vector3D.create(0, 0, 1)),
    }
    if direction not in dirs:
        return
    eye_dir, up = dirs[direction]
    eye_dir.normalize()
    dist = cam.eye.distanceTo(cam.target)
    cam.eye = adsk.core.Point3D.create(
        cam.target.x + eye_dir.x * dist,
        cam.target.y + eye_dir.y * dist,
        cam.target.z + eye_dir.z * dist,
    )
    cam.upVector = up


# ═══════════════════════════════════════════════════════════════════════════════
# ADD-IN LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════

def run(context):
    global _app, _ui, _http_server, _http_thread, _secret

    _app = adsk.core.Application.get()
    _ui = _app.userInterface

    try:
        _secret = _load_secret()

        custom_event = _app.registerCustomEvent(CUSTOM_EVENT_ID)
        handler = MCPEventHandler()
        custom_event.add(handler)
        _handlers.append(handler)
        _handlers.append(custom_event)

        _http_server = HTTPServer(("127.0.0.1", PORT), MCPRequestHandler)
        _http_thread = threading.Thread(target=_http_server.serve_forever, daemon=True)
        _http_thread.start()

        auth_status = "token auth enabled" if _secret else "WARNING: no secret file — auth disabled"
        _ui.messageBox(
            f"FusionMCPBridge started on port {PORT}\n"
            f"({auth_status})\n"
            "Claude Code can now connect via the MCP server.",
            "FusionMCPBridge",
        )

    except Exception:
        _ui.messageBox(
            f"FusionMCPBridge failed to start:\n{traceback.format_exc()}",
            "FusionMCPBridge Error",
        )


def stop(context):
    global _http_server

    try:
        if _http_server:
            _http_server.shutdown()
            _http_server = None

        try:
            _app.unregisterCustomEvent(CUSTOM_EVENT_ID)
        except Exception:
            pass

        for h in _handlers:
            try:
                if isinstance(h, adsk.core.CustomEvent):
                    for stored in _handlers:
                        if isinstance(stored, adsk.core.CustomEventHandler):
                            try:
                                h.remove(stored)
                            except Exception:
                                pass
            except Exception:
                pass
        _handlers.clear()

    except Exception:
        pass
