"""A last-resort 500 that sits inside `CORSMiddleware`, so CORS decorates it.

Starlette's `ServerErrorMiddleware` is outside CORS, so a 500 it writes carries no
`Access-Control-Allow-Origin` and the browser hides it. See
`docs/architecture/authentication.md`.
"""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_logger = logging.getLogger(__name__)

#: Names nothing about the fault (NFR-26, NFR-35).
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
            # A started response cannot be followed by a 500.
            if response_started:
                raise
            # The path only: a query string can carry search terms or filters.
            _logger.exception("unhandled error serving %s %s", scope["method"], scope["path"])
            await JSONResponse({"detail": UNHANDLED_ERROR_DETAIL}, status_code=500)(
                scope, receive, send
            )
