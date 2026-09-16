"""Gunicorn serving policy for ``pregdos-web``.

The CLI validates the effective listener before calling this module. Using BaseApplication
keeps gunicorn.conf.py and GUNICORN_CMD_ARGS from silently overriding that listener.
"""

import logging
import ssl

from gunicorn import http, sock
from gunicorn.app.base import BaseApplication
from gunicorn.workers.gthread import TConn, ThreadWorker


HANDSHAKE_TIMEOUT = 5.0
IO_TIMEOUT = 120.0


class Connection(TConn):
    """HTTP/1 connection with bounded TLS and socket I/O in a pool thread.

    Gunicorn's gthread watchdog monitors the worker process, not blocked threads.
    Its stock TConn.init leaves sockets blocking indefinitely. Keep this small
    adapter covered by real socket tests whenever the pinned Gunicorn is upgraded.
    """

    handshake_timeout = HANDSHAKE_TIMEOUT
    io_timeout = IO_TIMEOUT

    def init(self):
        if not self.initialized:
            self.sock.settimeout(self.handshake_timeout)
            if self.cfg.is_ssl:
                # do_handshake_on_connect=True: the handshake inherits the timeout.
                try:
                    self.sock = sock.ssl_wrap_socket(self.sock, self.cfg)
                except TimeoutError as exc:
                    # A peer that stalls part-way through its ClientHello is routine on a
                    # public listener.  But TimeoutError is an OSError carrying errno None,
                    # and gthread only forgives EPIPE/ECONNRESET/ENOTCONN -- everything else
                    # goes to log.exception, so each slow client would print a traceback that
                    # reads like a server fault.  #110 is about this server's log misreporting
                    # its own state, so report it as the one-line event it is, the way
                    # gunicorn already reports a malformed ClientHello, and re-raise as the
                    # SSL close it is so gthread drops the connection quietly.
                    # `client` is an (address, port) tuple over TCP, but gunicorn also binds
                    # Unix sockets, where it is not.  Match gunicorn's own "from ip=%s".
                    peer = self.client[0] if isinstance(self.client, tuple) else self.client
                    logging.getLogger("gunicorn.error").warning(
                        "TLS handshake timed out from ip=%s", peer)
                    raise ssl.SSLError(ssl.SSL_ERROR_EOF, "TLS handshake timed out") from exc
            self.parser = http.get_parser(self.cfg, self.sock, self.client)
            self.initialized = True
        # gthread switches back to blocking mode before each call to init, including
        # keepalive requests. Reapply the I/O timeout every time.
        self.sock.settimeout(self.io_timeout)


class Worker(ThreadWorker):
    connection_class = Connection

    def enqueue_req(self, conn):
        if not isinstance(conn, self.connection_class):
            conn = self.connection_class(conn.cfg, conn.sock, conn.client, conn.server)
        super().enqueue_req(conn)


class Application(BaseApplication):
    def __init__(self, app, options):
        self.app = app
        self.options = options
        super().__init__()

    def load_config(self):
        for name, value in self.options.items():
            self.cfg.set(name, value)

    def load(self):
        return self.app


def serve(app, *, host, port, certfile=None, keyfile=None, workers=1, threads=8):
    Application(app, options(host, port, certfile, keyfile, workers, threads)).run()


def options(host, port, certfile=None, keyfile=None, workers=1, threads=8):
    # Brackets are required around IPv6 literals in Gunicorn's bind syntax.
    address = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return {
        "bind": f"{address}:{port}",
        "worker_class": Worker,
        "workers": workers,
        "threads": threads,
        "worker_connections": 128,
        "certfile": certfile,
        "keyfile": keyfile,
        "do_handshake_on_connect": True,
        "http_protocols": "h1",
        "timeout": 120,
        "graceful_timeout": 120,
        "keepalive": 2,
        "accesslog": "-",
        "errorlog": "-",
        # With direct TLS the socket determines the scheme, not client-supplied headers.
        "forwarded_allow_ips": "",
    }
