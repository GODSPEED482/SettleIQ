"""Canonical error envelope (spec 12.1) and non-enumerating failures (spec 11.5).

Envelope shape, DRAFT under OPEN-14: {"error": {"code": str, "message": str, ...}}.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


# TODO(G1 OPEN-14 Suprava): ratify this envelope, or change it here and in api/client.ts.
class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def not_found() -> ApiError:
    """The only response for unknown or inaccessible tenants and resources."""
    return ApiError(404, "NOT_FOUND", "Resource not found.")


def unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "Authentication required.")


def email_not_verified() -> ApiError:
    return ApiError(403, "EMAIL_NOT_VERIFIED", "A verified email is required.")


def missing_permission(permission: str) -> ApiError:
    return ApiError(403, "MISSING_PERMISSION", "Permission required.", permission=permission)


def not_implemented(task: str) -> ApiError:
    return ApiError(501, "NOT_IMPLEMENTED", "Not implemented yet.", task=task)


def _envelope(status: int, code: str, message: str, **details: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message, **details}}
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _envelope(exc.status, exc.code, exc.message, **exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return _envelope(422, "VALIDATION_ERROR", "Invalid request.", fields=fields)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            error = not_found()
            return _envelope(error.status, error.code, error.message)
        return _envelope(exc.status_code, "HTTP_ERROR", str(exc.detail))
