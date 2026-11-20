"""A local OTLP/HTTP-JSON sink for Claude Code telemetry (#502): one JSON line per export request, identity dropped.

Usage: python -I otlp_sink.py <out.jsonl> [port] [seconds]
Listens on 127.0.0.1 only (default port 4318) for at most `seconds` (default 300), so a run cannot hang a shell.
Point Claude Code at it with OTEL_EXPORTER_OTLP_PROTOCOL=http/json and OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:<port>.
Attributes that name a person or account are deleted before anything is written (DROP), so the file is safe to keep
locally; it is still never committed (it carries session ids and, with OTEL_LOG_TOOL_DETAILS=1, Bash commands).
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DROP = {"user.email", "user.account_uuid", "user.account_id", "organization.id", "user.id", "host.name", "host.id"}


def scrub(node: object) -> object:
    """`node` with every OTLP KeyValue whose key is in DROP removed, at any depth."""
    if isinstance(node, list):
        return [scrub(x) for x in node if not (isinstance(x, dict) and x.get("key") in DROP)]
    if isinstance(node, dict):
        return {k: scrub(v) for k, v in node.items()}
    return node


def main() -> None:
    out = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 4318
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                payload = scrub(json.loads(body))
            except ValueError:
                payload = {"unparsed_bytes": len(body), "content_type": self.headers.get("Content-Type")}
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps({"path": self.path, "payload": payload}) + "\n")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Timer(seconds, server.shutdown).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
