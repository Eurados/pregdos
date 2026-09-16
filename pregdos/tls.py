"""Keep the built-in server's TLS handshake out of its single accept loop."""

import ssl

from werkzeug.serving import WSGIRequestHandler


class ThreadedTLSContext(ssl.SSLContext):
    """Werkzeug wraps its listening socket; defer accepted sockets' handshakes.

    Must be paired with TLSRequestHandler and a threaded server.
    """

    def wrap_socket(self, sock, server_side=False, do_handshake_on_connect=True,
                    suppress_ragged_eofs=True, server_hostname=None, session=None):
        return super().wrap_socket(
            sock, server_side=server_side,
            do_handshake_on_connect=False if server_side else do_handshake_on_connect,
            suppress_ragged_eofs=suppress_ragged_eofs,
            server_hostname=server_hostname, session=session,
        )


class TLSRequestHandler(WSGIRequestHandler):
    """Handshake in the request thread, with a deadline only for the handshake."""

    handshake_timeout = 5.0

    def handle(self):
        if isinstance(self.connection, ssl.SSLSocket):
            previous_timeout = self.connection.gettimeout()
            try:
                self.connection.settimeout(self.handshake_timeout)
                self.connection.do_handshake()
            except (OSError, ValueError) as exc:
                # Returning lets socketserver finish and close the connection normally.
                # A stalled or malformed peer must not produce an unhandled traceback.
                self.log_error("TLS handshake failed: %s", exc)
                return
            finally:
                self.connection.settimeout(previous_timeout)
        super().handle()


def server_context(cert: str, key: str) -> ssl.SSLContext:
    context = ThreadedTLSContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context
