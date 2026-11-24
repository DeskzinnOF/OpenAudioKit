import asyncio
from io import BytesIO

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")
pytest.importorskip("httpx")

import httpx

from openaudiokit.api import APIConfig, create_app


@pytest.fixture
def tone() -> bytes:
    waveform = 0.1 * np.sin(2 * np.pi * 1000 * np.arange(16_000) / 16_000)
    target = BytesIO()
    sf.write(target, waveform, 16_000, format="WAV", subtype="FLOAT")
    return target.getvalue()


def request(method, path, *, config=APIConfig(), **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=create_app(config))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_analyze_and_qc(tone):
    headers = {"Content-Type": "audio/wav"}
    result = request("POST", "/v1/analyze", content=tone, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["audio"]["sample_rate"] == 16_000
    assert result.json()["metrics"]["peak_dbfs"] == pytest.approx(-20.0)
    assert result.json()["spectrum"]["centroid_hz"] == pytest.approx(1000.0, abs=1.0)
    report = request("POST", "/v1/qc?expected_sample_rate=48000", content=tone, headers=headers)
    assert report.status_code == 200, report.text
    assert [issue["code"] for issue in report.json()["issues"]] == ["sample_rate_mismatch"]


def test_compare(tone, monkeypatch):
    monkeypatch.setattr("openaudiokit.api._MIB", 64)
    files = {
        "reference": ("../ref.wav", tone, "audio/wav"),
        "candidate": ("../candidate.wav", tone, "audio/wav"),
    }
    config = APIConfig(max_upload_bytes=len(tone))
    result = request("POST", "/v1/compare", config=config, files=files)
    assert result.status_code == 200, result.text
    assert "../" not in result.text
    assert result.json()["deltas"]["rms_dbfs"] == pytest.approx(0.0)
    files["reference"] = ("ref.wav", tone + b"x", "audio/wav")
    limit = request("POST", "/v1/compare", config=config, files=files)
    assert limit.status_code == 413
    assert limit.json()["error"]["code"] == "upload_limit"


def test_comparison_requires_two_named_files_and_a_closing_boundary(tone):
    missing = request("POST", "/v1/compare", files={"reference": ("ref.wav", tone)})
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "invalid_multipart"
    truncated = request(
        "POST",
        "/v1/compare",
        headers={"Content-Type": "multipart/form-data; boundary=boundary"},
        content=(
            b'--boundary\r\nContent-Disposition: form-data; name="reference"; filename="r.wav"'
            b"\r\n\r\n"
            + tone
            + b'\r\n--boundary\r\nContent-Disposition: form-data; name="candidate"; filename="c.wav"'
            b"\r\n\r\n" + tone
        ),
    )
    assert truncated.status_code == 400


def test_normalize_returns_float_wave(tone):
    result = request(
        "POST",
        "/v1/normalize?target=-3",
        content=tone,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert result.status_code == 200, result.text
    assert result.headers["content-type"] == "audio/wav"
    audio, rate = sf.read(BytesIO(result.content))
    assert rate == 16_000
    assert np.max(np.abs(audio)) == pytest.approx(10 ** (-3 / 20))


@pytest.mark.parametrize("content_length", [None, "1", "128"])
def test_upload_limit_is_based_on_real_bytes(content_length):
    async def stream():
        yield b"x" * 32
        yield b"x" * 32

    headers = {"Content-Type": "audio/wav"}
    if content_length is not None:
        headers["Content-Length"] = content_length
    result = request(
        "POST",
        "/v1/analyze",
        config=APIConfig(max_upload_bytes=48),
        content=stream(),
        headers=headers,
    )
    assert result.status_code == 413
    assert result.json()["error"]["code"] == "upload_limit"


def test_rejects_invalid_audio_and_queries(tone):
    headers = {"Content-Type": "audio/wav"}
    decoded = request(
        "POST",
        "/v1/analyze",
        config=APIConfig(max_decoded_samples=10),
        content=tone,
        headers=headers,
    )
    assert decoded.status_code == 413
    assert decoded.json()["error"]["code"] == "decoded_limit"
    invalid = request("POST", "/v1/analyze", content=b"garbage", headers=headers)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_audio"
    unsupported = request("POST", "/v1/analyze", content=tone)
    assert unsupported.status_code == 415
    query = request("POST", "/v1/normalize?target=nan", content=tone, headers=headers)
    assert query.status_code == 422
    assert query.json()["error"]["code"] == "invalid_request"


def test_timeout_releases_the_job_slot(tone):
    async def slow_upload():
        await asyncio.sleep(1)
        yield tone

    async def send():
        transport = httpx.ASGITransport(app=create_app(APIConfig(upload_timeout_seconds=0.2)))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Content-Type": "audio/wav"}
            timed_out = await client.post("/v1/analyze", content=slow_upload(), headers=headers)
            assert timed_out.status_code == 408
            next_request = await client.post("/v1/analyze", content=tone, headers=headers)
            assert next_request.status_code == 200, next_request.text

    asyncio.run(send())


def test_concurrent_uploads_respect_job_limit(tone):
    async def send():
        started = asyncio.Event()
        finish = asyncio.Event()

        async def delayed_upload():
            started.set()
            await finish.wait()
            yield tone

        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Content-Type": "audio/wav"}
            first = asyncio.create_task(
                client.post("/v1/analyze", content=delayed_upload(), headers=headers)
            )
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                busy = await client.post("/v1/analyze", content=tone, headers=headers)
                assert busy.status_code == 503
                assert busy.json()["error"]["code"] == "busy"
                assert (await client.get("/health")).json() == {"status": "ok"}
            finally:
                finish.set()
                assert (await asyncio.wait_for(first, timeout=2)).status_code == 200

    asyncio.run(send())


def test_openapi_documents_binary_inputs_and_outputs():
    result = request("GET", "/openapi.json")
    assert result.status_code == 200
    operations = result.json()["paths"]
    compare_body = operations["/v1/compare"]["post"]["requestBody"]
    assert "multipart/form-data" in compare_body["content"]
    normalize_response = operations["/v1/normalize"]["post"]["responses"]["200"]
    assert "audio/wav" in normalize_response["content"]


def test_browser_origin_checked_before_processing(tone):
    headers = {"Content-Type": "audio/wav", "Origin": "https://other.example"}
    denied = request("POST", "/v1/analyze", content=tone, headers=headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "origin_not_allowed"
    headers["Origin"] = "http://test"
    allowed = request("POST", "/v1/analyze", content=tone, headers=headers)
    assert allowed.status_code == 200


def test_loopback_cli_rejects_foreign_host(monkeypatch):
    import sys

    import uvicorn

    from openaudiokit import api

    monkeypatch.setattr(api, "app", create_app())
    monkeypatch.setattr(sys, "argv", ["openaudiokit-api"])
    observed = []

    def capture(app, *, host, port):
        assert (host, port) == ("127.0.0.1", 8000)
        observed.append(app)

    monkeypatch.setattr(uvicorn, "run", capture)
    api.main()

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=observed[0]), base_url="http://127.0.0.1:8000"
        ) as client:
            assert (await client.get("/health")).status_code == 200
            denied = await client.get("/health", headers={"Host": "other.example"})
            assert denied.status_code == 400
            assert denied.json()["error"]["code"] == "invalid_host"

    asyncio.run(send())
