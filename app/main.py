"""FastAPI entry points and uniform error envelopes."""

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.config import Failure, Settings
from app.schemas import ErrorResponse, Response
from app.schemas import Request as LinkRequest
from app.services.analysis_service import AnalysisService

app = FastAPI(title="Brandorgalytics", version="1.0")
service = AnalysisService(Settings())


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"success": False, "error": {"code": code, "message": message}}, status
    )


@app.exception_handler(Failure)
async def known_error(request: Request, exc: Failure) -> JSONResponse:
    return error(exc.status, exc.code, str(exc))


@app.exception_handler(RequestValidationError)
async def bad_input(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error(
        422, "invalid_request", "Provide a JSON object containing a website URL."
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return error(
        exc.status_code, "http_error", "This resource or method is unavailable."
    )


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception) -> JSONResponse:
    return error(500, "internal_error", "An unexpected error occurred.")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/link-analysis",
    response_model=Response,
    responses={
        status: {"model": ErrorResponse} for status in (422, 429, 500, 502, 504)
    },
)
async def analyze(payload: LinkRequest, request: Request) -> Response:
    service.admit(request.client.host if request.client else "unknown")
    return await service.run(payload.url)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=service.settings.port)
