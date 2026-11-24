"""Optional local REST service with bounded, anonymous audio uploads."""

from __future__ import annotations

import argparse
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from io import BytesIO
from tempfile import SpooledTemporaryFile
from threading import BoundedSemaphore
from typing import Annotated, BinaryIO, Literal, cast

from anyio import fail_after
from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from python_multipart import MultipartParser
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from .analysis import AnalysisResult, analyze
from .audio import Audio, load_audio, write_audio
from .config import QCConfig
from .dsp import normalize_loudness, normalize_peak
from .errors import AudioError, AudioLimitError
from .qc import ComparisonResult, QCReport, compare, inspect
from .spectral import SpectralSummary, spectral_summary

_MIB = 1024 * 1024
_MAX_PART_HEADERS = 8 * 1024
_MULTIPART_OVERHEAD = 32 * 1024
_BINARY_SCHEMA = {"type": "string", "format": "binary"}
_RAW_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": _BINARY_SCHEMA}},
    },
}
_COMPARE_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {"reference": _BINARY_SCHEMA, "candidate": _BINARY_SCHEMA},
                    "required": ["reference", "candidate"],
                },
            },
        },
    },
}


@dataclass(frozen=True)
class APIConfig:
    """Limits per app process. A decoded sample is one value in one channel."""

    max_upload_bytes: int = 16 * _MIB
    max_decoded_samples: int = 8_000_000
    max_concurrent_jobs: int = 1
    upload_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        limits = (self.max_upload_bytes, self.max_decoded_samples, self.max_concurrent_jobs)
        if any(type(limit) is not int or limit < 1 for limit in limits):
            raise ValueError("API byte, sample and concurrency limits must be positive integers.")
        if not math.isfinite(self.upload_timeout_seconds) or self.upload_timeout_seconds <= 0:
            raise ValueError("The upload timeout must be finite and positive.")


@dataclass(frozen=True)
class AudioMetadata:
    """Decoded audio properties returned without a source location."""

    sample_rate: int
    frames: int
    channels: int
    duration_seconds: float


@dataclass(frozen=True)
class AnalysisResponse:
    """Time-domain analysis and spectral summary of one uploaded file."""

    audio: AudioMetadata
    metrics: AnalysisResult
    spectrum: SpectralSummary


@dataclass(frozen=True)
class _Error:
    code: str
    message: str


@dataclass(frozen=True)
class _ErrorResponse:
    error: _Error


class _UploadError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _oversize() -> _UploadError:
    return _UploadError(413, "upload_limit", "The upload exceeded the byte limit.")


def _bad_multipart() -> _UploadError:
    return _UploadError(
        400, "invalid_multipart", "Supply exactly two complete file parts: reference and candidate."
    )


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _check_length(request: Request, limit: int) -> int | None:
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise _UploadError(415, "unsupported_media_type", "Content-Encoding must be identity.")
    lengths = request.headers.getlist("content-length")
    if not lengths:
        return None
    try:
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise ValueError
        length = int(lengths[0])
    except ValueError as exc:
        raise _UploadError(400, "invalid_request", "Invalid Content-Length header.") from exc
    if length > limit:
        raise _oversize()
    return length


async def _receive(
    request: Request, write: Callable[[bytes], int], *, limit: int, timeout: float
) -> None:
    length = _check_length(request, limit)
    received = 0
    try:
        with fail_after(timeout):
            async for chunk in request.stream():
                if not chunk:
                    continue
                received += len(chunk)
                if received > limit:
                    raise _oversize()
                await run_in_threadpool(write, chunk)
    except TimeoutError as exc:
        raise _UploadError(408, "upload_timeout", "The upload exceeded the time limit.") from exc
    except ClientDisconnect as exc:
        raise _UploadError(400, "incomplete_upload", "The upload was interrupted.") from exc
    if length is not None and length != received:
        raise _UploadError(
            400, "incomplete_upload", "The upload size did not match Content-Length."
        )


@contextmanager
def _upload_spool() -> Iterator[BinaryIO]:
    with SpooledTemporaryFile(max_size=_MIB, mode="w+b") as upload:
        # SpooledTemporaryFile implements the seekable binary stream interface.
        yield cast(BinaryIO, upload)


@asynccontextmanager
async def _raw_upload(request: Request, config: APIConfig) -> AsyncIterator[BinaryIO]:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/octet-stream" and not media_type.startswith("audio/"):
        raise _UploadError(
            415, "unsupported_media_type", "Use application/octet-stream or audio/*."
        )
    with _upload_spool() as upload:
        await _receive(
            request,
            upload.write,
            limit=config.max_upload_bytes,
            timeout=config.upload_timeout_seconds,
        )
        upload.seek(0)
        yield upload


class _ComparisonForm:
    """Validate and spool exactly two named parts using python-multipart's framing parser."""

    def __init__(
        self, request: Request, reference: BinaryIO, candidate: BinaryIO, limit: int
    ) -> None:
        media_type, options = parse_options_header(request.headers.get("content-type", ""))
        if media_type != b"multipart/form-data":
            raise _UploadError(415, "unsupported_media_type", "Use multipart/form-data.")
        boundary = options.get(b"boundary", b"")
        if not 0 < len(boundary) <= 70 or not boundary.isascii():
            raise _bad_multipart()

        self.files = {b"reference": reference, b"candidate": candidate}
        self.seen: set[bytes] = set()
        self.limit = limit
        self.part: BinaryIO | None = None
        self.part_bytes = 0
        self.header_bytes = 0
        self.field = bytearray()
        self.value = bytearray()
        self.disposition = b""
        self.complete = False
        self.parser = MultipartParser(
            boundary,
            {
                "on_part_begin": self._begin_part,
                "on_part_data": self._append_data,
                "on_header_field": self._append_field,
                "on_header_value": self._append_value,
                "on_header_end": self._end_header,
                "on_headers_finished": self._select_file,
                "on_end": self._end_form,
            },
        )

    def _begin_part(self) -> None:
        if len(self.seen) == len(self.files):
            raise _bad_multipart()
        self.part = None
        self.part_bytes = 0
        self.header_bytes = 0
        self.disposition = b""

    def _add_header(self, buffer: bytearray, data: bytes, start: int, end: int) -> None:
        self.header_bytes += end - start
        if self.header_bytes > _MAX_PART_HEADERS:
            raise _oversize()
        buffer.extend(data[start:end])

    def _append_field(self, data: bytes, start: int, end: int) -> None:
        self._add_header(self.field, data, start, end)

    def _append_value(self, data: bytes, start: int, end: int) -> None:
        self._add_header(self.value, data, start, end)

    def _end_header(self) -> None:
        if self.field.lower() == b"content-disposition":
            if self.disposition:
                raise _bad_multipart()
            self.disposition = bytes(self.value)
        self.field.clear()
        self.value.clear()

    def _select_file(self) -> None:
        disposition, options = parse_options_header(self.disposition)
        name = options.get(b"name", b"")
        if (
            disposition != b"form-data"
            or b"filename" not in options
            or name not in self.files
            or name in self.seen
        ):
            raise _bad_multipart()
        self.part = self.files[name]
        self.seen.add(name)

    def _append_data(self, data: bytes, start: int, end: int) -> None:
        assert self.part is not None
        self.part_bytes += end - start
        if self.part_bytes > self.limit:
            raise _oversize()
        self.part.write(data[start:end])

    def _end_form(self) -> None:
        self.complete = True

    async def read(self, request: Request, timeout: float) -> None:
        try:
            await _receive(
                request,
                self.parser.write,
                limit=2 * self.limit + _MULTIPART_OVERHEAD,
                timeout=timeout,
            )
            self.parser.finalize()
        except FormParserError as exc:
            raise _bad_multipart() from exc
        if not self.complete or self.seen != self.files.keys():
            raise _bad_multipart()
        for upload in self.files.values():
            upload.seek(0)


def _analysis(audio: Audio) -> AnalysisResponse:
    return AnalysisResponse(
        AudioMetadata(audio.sample_rate, audio.frames, audio.channels, audio.duration),
        analyze(audio),
        spectral_summary(audio),
    )


def create_app(config: APIConfig = APIConfig()) -> FastAPI:
    """Build an app with configurable resource limits and versioned endpoints."""
    app = FastAPI(title="OpenAudioKit", version="v1", description="Offline audio analysis and QC.")
    slots = BoundedSemaphore(config.max_concurrent_jobs)

    @app.exception_handler(_UploadError)
    async def upload_error(_request: Request, exc: _UploadError) -> JSONResponse:
        return _error(exc.status, exc.code, str(exc))

    @app.exception_handler(AudioLimitError)
    async def decoded_limit(_request: Request, _exc: AudioLimitError) -> JSONResponse:
        return _error(413, "decoded_limit", "Decoded audio exceeded the sample limit.")

    @app.exception_handler(AudioError)
    async def audio_error(_request: Request, _exc: AudioError) -> JSONResponse:
        return _error(
            422, "invalid_audio", "The uploaded audio is invalid or unsupported for this operation."
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        locations = {".".join(str(part) for part in error["loc"]) for error in exc.errors()}
        return _error(
            422, "invalid_request", f"Invalid request parameters: {', '.join(sorted(locations))}."
        )

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        response = _error(exc.status_code, "http_error", str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def server_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error(500, "server_error", "The server could not complete the operation.")

    async def acquire_slot(request: Request) -> AsyncIterator[None]:
        origins = request.headers.getlist("origin")
        own_origin = f"{request.url.scheme}://{request.url.netloc}"
        if origins and origins != [own_origin]:
            raise _UploadError(403, "origin_not_allowed", "Use the API from the same origin.")
        if not slots.acquire(blocking=False):
            raise _UploadError(503, "busy", "All audio processing slots are in use.")
        try:
            yield
        finally:
            slots.release()

    def decode(source: BinaryIO) -> Audio:
        return load_audio(source, max_samples=config.max_decoded_samples)

    router = APIRouter(
        prefix="/v1",
        dependencies=[Depends(acquire_slot)],
        responses={
            code: {"model": _ErrorResponse, "description": description}
            for code, description in {
                400: "Malformed or incomplete upload",
                403: "Cross-origin browser request rejected",
                408: "Upload timed out",
                413: "Upload or decoded sample limit exceeded",
                415: "Unsupported request media type or encoding",
                422: "Invalid audio or request parameters",
                500: "Internal error",
                503: "All processing slots are occupied",
            }.items()
        },
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Check whether the HTTP service is running."""
        return {"status": "ok"}

    @router.post("/analyze", openapi_extra=_RAW_OPENAPI)
    async def analyze_upload(request: Request) -> AnalysisResponse:
        """Measure summary loudness, dynamics, stereo, level and spectral features."""
        async with _raw_upload(request, config) as upload:
            return await run_in_threadpool(lambda: _analysis(decode(upload)))

    @router.post("/qc", openapi_extra=_RAW_OPENAPI)
    async def qc_upload(
        request: Request,
        expected_sample_rate: Annotated[int | None, Query(ge=1, le=384_000)] = None,
    ) -> QCReport:
        """Inspect an audio file for configured objective quality issues."""
        settings = QCConfig(expected_sample_rate=expected_sample_rate)
        async with _raw_upload(request, config) as upload:
            return await run_in_threadpool(lambda: inspect(decode(upload), settings))

    @router.post("/compare", openapi_extra=_COMPARE_OPENAPI)
    async def compare_uploads(request: Request) -> ComparisonResult:
        """Compare a reference and a candidate using objective audio measurements."""
        with _upload_spool() as reference, _upload_spool() as candidate:
            await _ComparisonForm(request, reference, candidate, config.max_upload_bytes).read(
                request, config.upload_timeout_seconds
            )
            return await run_in_threadpool(lambda: compare(decode(reference), decode(candidate)))

    @router.post(
        "/normalize",
        openapi_extra=_RAW_OPENAPI,
        response_class=Response,
        responses={200: {"content": {"audio/wav": {"schema": _BINARY_SCHEMA}}}},
    )
    async def normalize_upload(
        request: Request,
        mode: Literal["peak", "loudness"] = "peak",
        target: Annotated[float | None, Query(ge=-300, le=0)] = None,
        ceiling_dbtp: Annotated[float, Query(ge=-300, le=0)] = -1.0,
    ) -> Response:
        """Normalize to dBFS or LUFS and return a 32-bit float WAV."""

        if mode == "loudness" and target is not None and target < -69:
            raise _UploadError(422, "invalid_request", "Loudness target must be in [-69, 0] LUFS.")

        def render(upload: BinaryIO) -> bytes:
            audio = decode(upload)
            if mode == "peak":
                normalized = normalize_peak(audio, -1.0 if target is None else target)
            else:
                normalized = normalize_loudness(
                    audio, -23.0 if target is None else target, ceiling_dbtp=ceiling_dbtp
                )
            with BytesIO() as output:
                write_audio(output, normalized, format="WAV", subtype="FLOAT")
                return output.getvalue()

        async with _raw_upload(request, config) as upload:
            wav = await run_in_threadpool(render, upload)
        return Response(
            wav,
            media_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="normalized.wav"'},
        )

    app.include_router(router)
    return app


app = create_app()


def main() -> None:
    """Start the optional REST service using Uvicorn."""
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the OpenAudioKit REST service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65_535:
        parser.error("--port must be in 1..65535")
    if args.host in {"localhost", "127.0.0.1", "::1"}:
        names = {"localhost", "127.0.0.1", "[::1]"}
        hosts = names | {f"{name}:{args.port}" for name in names}

        @app.middleware("http")
        async def check_loopback_host(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            values = request.headers.getlist("host")
            if len(values) != 1 or values[0] not in hosts:
                return _error(400, "invalid_host", "Use a loopback host for this service.")
            return await call_next(request)

        uvicorn.run(app, host=args.host, port=args.port)
    else:
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
