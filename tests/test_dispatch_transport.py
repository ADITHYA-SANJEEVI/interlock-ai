"""Actual TCP/HTTP client failures, counted independently of application mocks."""

import dataclasses
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from interlock import dispatch_broker
from interlock.config import settings
from interlock.contracts import digest
from test_dispatch import auth as auth
from test_dispatch import d as d
from test_dispatch import record

ACTUAL_HTTP = dispatch_broker.reference_call


@pytest.mark.parametrize("failure", ["reset", "timeout", "500", "502", "503", "interrupted"])
def test_actual_http_transport_never_retries_mutation(d, monkeypatch, failure):
    op, grant, _ = d.op()
    requests = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            import json

            raw = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append({"method": "POST", "path": self.path, "body_digest": digest(json.loads(raw))})
            if failure == "timeout":
                release.wait(15)
                return
            if failure in {"reset", "interrupted"}:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(int(failure))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    configuration = dataclasses.replace(settings(), reference_url=f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(dispatch_broker, "settings", lambda: configuration)
    monkeypatch.setattr(dispatch_broker, "reference_call", ACTUAL_HTTP)
    try:
        response = d.send(op)
        assert response.status_code == 200 and response.json()["knowledge"] == "UNKNOWN"
        assert len(requests) == 1
        assert requests == [
            {"method": "POST", "path": "/dispatch/orders", "body_digest": op["request_digest"]}
        ]
        assert d.send(op, {"claim_id": "redelivery"}).status_code == 409
        assert len(requests) == 1
        assert list(d.auth.balance(grant)) == [100, 0, 100, 0, "ACTIVE"]
        record(
            "actual_sdk_tcp_" + failure,
            classification="VERIFIED_AUTOMATED",
            requests=requests,
            counts=d.counts(op),
            state=d.state(op),
            no_hidden_retry=True,
        )
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
