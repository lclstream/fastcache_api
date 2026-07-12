from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class CacheNotFound(Exception):
    pass


class CacheKeyConflict(Exception):
    pass


class CachePortsExhausted(Exception):
    pass


class CacheStartFailed(Exception):
    pass


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(CacheNotFound)
    async def _handle_not_found(_request: Request, exc: CacheNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) or "Cache not found"},
        )

    @app.exception_handler(CacheKeyConflict)
    async def _handle_key_conflict(
        _request: Request, exc: CacheKeyConflict
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc) or "Cache key conflict"},
        )

    @app.exception_handler(CachePortsExhausted)
    async def _handle_ports_exhausted(
        _request: Request, exc: CachePortsExhausted
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc) or "No free cache ports"},
        )

    @app.exception_handler(CacheStartFailed)
    async def _handle_start_failed(
        _request: Request, exc: CacheStartFailed
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc) or "Failed to start cache process"},
        )
