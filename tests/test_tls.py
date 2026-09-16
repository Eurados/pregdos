"""Real socket regressions for #110, including the threaded serving path."""

from contextlib import contextmanager
import http.client
import shutil
import socket
import ssl
import subprocess
import threading
import time

import pytest
from werkzeug.serving import make_server

from pregdos.tls import TLSRequestHandler, server_context

# These need a certificate, and `openssl` is the one way to make one without adding a
# dependency.  PregDos supports the local backend on a plain Windows workstation (#91),
# where it is usually absent -- skip there rather than fail the whole module on a missing
# binary that has nothing to do with what is being tested.
pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl to make a certificate")


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    directory = tmp_path_factory.mktemp("tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
    ], check=True, capture_output=True)
    return str(cert), str(key)


@contextmanager
def serving(certificate, *, tls=True, timeout=5.0, delay=0):
    entered = threading.Event()

    class Handler(TLSRequestHandler):
        handshake_timeout = timeout

        def handle(self):
            entered.set()
            super().handle()

    def app(environ, start_response):
        # Exercise response streaming beyond the handshake deadline, too.
        start_response("200 OK", [("Content-Type", "text/plain")])
        yield b"hello "
        time.sleep(delay)
        yield b"world"

    server = make_server(
        "127.0.0.1", 0, app, threaded=True, request_handler=Handler,
        ssl_context=server_context(*certificate) if tls else None,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, entered
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def fetch(port, *, tls=True):
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection("127.0.0.1", port, timeout=1, context=context)
    else:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == b"hello world"
    finally:
        connection.close()


@pytest.mark.parametrize("tls", [True, False])
def test_silent_clients_do_not_block_other_requests(certificate, tls):
    with serving(certificate, tls=tls) as (port, entered):
        # Five seconds per silent peer would exceed the real request's one-second timeout.
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            assert entered.wait(2), "connection never reached its request thread"
            entered.clear()
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                assert entered.wait(2)
                fetch(port, tls=tls)


@pytest.mark.parametrize("payload", [b"", b"\x16\x03\x01\x00\x80"])
def test_silent_or_incomplete_handshake_is_closed(certificate, payload, caplog):
    with serving(certificate, timeout=0.2) as (port, entered):
        with socket.create_connection(("127.0.0.1", port), timeout=2) as peer:
            if payload:
                peer.sendall(payload)
            assert entered.wait(2)
            assert peer.recv(1) == b""
        fetch(port)
    assert "TLS handshake failed" in caplog.text


def test_malformed_handshake_does_not_break_server(certificate, caplog):
    with serving(certificate) as (port, entered):
        with socket.create_connection(("127.0.0.1", port), timeout=2) as peer:
            peer.sendall(b"GET / HTTP/1.0\r\n\r\n")
            assert entered.wait(2)
            try:
                assert peer.recv(1) == b""
            except ConnectionResetError:
                pass
        fetch(port)
    assert "TLS handshake failed" in caplog.text


def test_handshake_deadline_does_not_limit_http_reads(certificate):
    with serving(certificate, timeout=0.2, delay=0.4) as (port, entered):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", port), timeout=2) as raw:
            with context.wrap_socket(raw, server_hostname="localhost") as peer:
                assert entered.wait(2)
                time.sleep(0.4)
                peer.sendall(b"GET / HTTP/1.0\r\n\r\n")
                with http.client.HTTPResponse(peer) as response:
                    response.begin()
                    assert response.status == 200
                    assert response.read() == b"hello world"
