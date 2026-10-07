"""A last-resort 500 that sits inside `CORSMiddleware`.

Starlette's own `ServerErrorMiddleware` is the outermost layer, outside CORS. An
exception no handler claims passes through `CORSMiddleware` untouched and that outer
layer writes the 500, so the response carries no `Access-Control-Allow-Origin`. A
cross-origin browser then hides the status and the SPA sees an opaque network error
instead of a 500 it can report. Registering `app.exception_handler(Exception)` does
not help: Starlette routes that handler to `ServerErrorMiddleware` as well.

This middleware catches what escapes the routes and `nptc.api.errors` handlers and
turns it into an ordinary JSON response, which CORS then decorates like any other.
"""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_logger = logging.getLogger(__name__)

#: Names nothing about the fault (NFR-26, NFR-35): exception text is diagnostic and
#: can hold identifiers, so it goes to the log, never the response.
UNHANDLED_ERROR_DETAIL = (
    "This request could not be completed. Contact an administrator if the problem persists."
)


class UnhandledErrorMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            # Once the status line has gone out there is no 500 left to send; the
            # outer layer closes the connection.
            if response_started:
                raise
            # The path only: a query string can carry search terms or filters.
            _logger.exception("unhandled error serving %s %s", scope["method"], scope["path"])
            await JSONResponse({"detail": UNHANDLED_ERROR_DETAIL}, status_code=500)(
                scope, receive, send
            )
