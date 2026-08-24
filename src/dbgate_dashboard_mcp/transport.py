"""An httpx transport that connects through an ssh tunnel without lying about
who it is talking to.

``ssh -L`` forwards TCP, so pointing the client at ``127.0.0.1:<local_port>``
reaches the dashboard — but it also rewrites the request's *identity*: the URL
host decides both the ``Host`` header and the TLS SNI name. Against a public
HTTPS vhost that breaks twice over: the reverse proxy 308-redirects a ``Host``
carrying a non-canonical port, and certificate verification fails against a
``127.0.0.1`` SNI.

So the connection address is overridden at the transport layer instead, and
the request keeps the hostname it was built with. ``Host`` is already correct
— httpx fills it in at construction, before this code runs — and ``sni_hostname``
is an httpcore request extension that pins the TLS handshake (and therefore
certificate validation) to the real name.
"""

from __future__ import annotations

import httpx


def retarget(request: httpx.Request, host: str, port: int) -> None:
    """Send ``request`` to ``host:port`` while keeping its original identity.

    Mutates in place: the scheme, path, body and ``Host`` header are untouched,
    only the connect address moves. The hostname the URL carried is preserved
    as ``sni_hostname`` so TLS still negotiates — and verifies — against it.
    """
    request.extensions = {**request.extensions, "sni_hostname": request.url.host}
    request.url = request.url.copy_with(host=host, port=port)


class TunneledTransport(httpx.AsyncHTTPTransport):
    """``AsyncHTTPTransport`` that dials a forwarded local port.

    Extra keyword arguments go straight to httpx, so TLS options such as
    ``verify`` keep working — and stay on by default, which is the point of
    preserving SNI.
    """

    def __init__(self, host: str, port: int, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._host = host
        self._port = port

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        retarget(request, self._host, self._port)
        return await super().handle_async_request(request)
