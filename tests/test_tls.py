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


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    # OpenSSL may be absent on Windows. Only TLS cases need this fixture; plain HTTP
    # regressions must still run there without a certificate-generation dependency.
    if shutil.which("openssl") is None:
        pytest.skip("needs openssl to make a certificate")
    directory = tmp_path_factory.mktemp("tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
    ], check=True, capture_output=True)
    return str(cert), str(key)


@pytest.fixture
def transport_certificate(request, tls):
    return request.getfixturevalue("certificate") if tls else None


@contextmanager
def serving(certificate, *, tls=True, timeout=5.0, io_timeout=TLSRequestHandler.timeout, delay=0):
    entered = threading.Event()
    handshake_deadline = timeout

    class Handler(TLSRequestHandler):
        handshake_timeout = handshake_deadline
        timeout = io_timeout

        def handle(self):
            entered.set()
            super().handle()

    def app(environ, start_response):
        if environ.get("CONTENT_LENGTH"):
            length = int(environ["CONTENT_LENGTH"])
            assert environ["wsgi.input"].read(length) == b"x" * length
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
def test_silent_clients_do_not_block_other_requests(transport_certificate, tls):
    with serving(transport_certificate, tls=tls) as (port, entered):
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


@contextmanager
def connected(port, tls):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as raw:
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            with context.wrap_socket(raw, server_hostname="localhost") as peer:
                yield peer
        else:
            yield raw


@pytest.mark.parametrize("tls", [True, False])
@pytest.mark.parametrize("payload", [
    b"",
    b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Unfinished:",
    b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10\r\n\r\nx",
])
def test_stalled_http_request_is_closed(transport_certificate, tls, payload):
    with serving(transport_certificate, tls=tls, io_timeout=0.2) as (port, entered):
        with connected(port, tls) as peer:
            if payload:
                peer.sendall(payload)
            assert entered.wait(2)
            # A response is permitted, but the server must close instead of retaining
            # the request thread forever. The client's longer timeout bounds the test.
            while peer.recv(4096):
                pass
        fetch(port, tls=tls)


@pytest.mark.parametrize("tls", [True, False])
def test_io_timeout_allows_progress_and_long_response_generation(transport_certificate, tls):
    with serving(transport_certificate, tls=tls, io_timeout=0.4, delay=0.6) as (port, entered):
        with connected(port, tls) as peer:
            peer.sendall(b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 8\r\n\r\n")
            assert entered.wait(2)
            # Total upload time exceeds the timeout, but each read makes progress.
            for _ in range(8):
                peer.sendall(b"x")
                time.sleep(0.1)
            with http.client.HTTPResponse(peer) as response:
                response.begin()
                assert response.status == 200
                assert response.read() == b"hello world"
