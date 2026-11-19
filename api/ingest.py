"""POST /api/ingest — body: the raw .msg file; header x-upload-key.
Returns a draft edition (not saved). The upload page shows it for review, then calls /api/publish."""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))
import _gulfmsg  # noqa: E402

MAX_BYTES = 4 * 1024 * 1024


class handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        key = os.environ.get("UPLOAD_KEY", "")
        if not key or not hmac.compare_digest(self.headers.get("x-upload-key", ""), key):
            return self._send(403, {"error": "This upload link is not valid. Use the full link you were given."})
        n = int(self.headers.get("content-length") or 0)
        if not n or n > MAX_BYTES:
            return self._send(400, {"error": "Choose the tracker email saved as a .msg file (under 4 MB)."})
        try:
            draft = _gulfmsg.parse_msg(self.rfile.read(n))
        except ValueError as e:
            return self._send(422, {"error": str(e)})
        except Exception:
            return self._send(422, {"error": "That file could not be read. Save the email from Outlook as .msg and try again."})
        draft["stories"] = _gulfmsg.enrich(draft["stories"])
        return self._send(200, draft)
