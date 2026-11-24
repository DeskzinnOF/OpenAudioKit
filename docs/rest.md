# Optional REST service

Install the API extra and start the local server.

```console
python -m pip install -e ".[api]"
openaudiokit-api --host 127.0.0.1 --port 8000
```

Open the [interactive API documentation](http://127.0.0.1:8000/docs). The OpenAPI schema is available at `/openapi.json` and `GET /health` returns `{"status":"ok"}`.

| Endpoint | Request | Success response |
| --- | --- | --- |
| `POST /v1/analyze` | Raw audio file | Decoded metadata, time-domain metrics and spectral summary as JSON |
| `POST /v1/qc` | Raw audio file and optional `expected_sample_rate` query parameter (Hz) | Objective quality findings as JSON |
| `POST /v1/compare` | Multipart form with two file parts named `reference` and `candidate` | Objective comparison as JSON |
| `POST /v1/normalize` | Raw audio file and normalization parameters in the query string | 32-bit floating-point WAV download |

Single-file endpoints accept `application/octet-stream` and `audio/*`. They require the encoded audio bytes themselves. Supported encodings depend on the installed libsndfile build. `null` represents unavailable measurements in JSON.

```bash
curl --fail-with-body -H 'Content-Type: application/octet-stream' \
  --data-binary @mix.wav http://127.0.0.1:8000/v1/analyze

curl --fail-with-body -H 'Content-Type: application/octet-stream' \
  --data-binary @mix.wav 'http://127.0.0.1:8000/v1/qc?expected_sample_rate=48000'

curl --fail-with-body -F 'reference=@reference.wav' -F 'candidate=@mix.wav' \
  http://127.0.0.1:8000/v1/compare

curl --fail-with-body -H 'Content-Type: application/octet-stream' \
  --data-binary @mix.wav --output normalized.wav \
  'http://127.0.0.1:8000/v1/normalize?mode=loudness&target=-23&ceiling_dbtp=-1'
```

Normalization defaults to `mode=peak` at −1 dBFS and accepts targets from −300 to 0 dBFS. Loudness mode defaults to −23 LUFS with a −1 dBTP ceiling, and accepts targets from −69 to 0 LUFS. A binding peak ceiling can leave the output below the requested loudness.

Uploads have a default limit of 16 MiB and 8,000,000 decoded sample values per file. Each channel counts separately toward the decoded limit. This permits about 83 seconds of stereo audio at 48 kHz. A comparison can upload two files within those per-file limits. The app allows one active audio job per process and 30 seconds to receive a request body by default. Uploads spool to server-generated temporary files when they exceed 1 MiB. The app closes them after the request. Multipart filenames have no effect on file handling.

Errors use the same JSON structure across endpoints, for example `{"error":{"code":"upload_limit","message":"The upload exceeded the byte limit."}}`. Expect HTTP 400 for malformed uploads, 408 for upload timeout, 413 for byte or decoded-sample limits, 415 for an unsupported request content type or encoding, 422 for invalid audio or parameters, and 503 when the app has no free processing slot.

Programmatic deployments can pass an `APIConfig` to `openaudiokit.api.create_app`. The service is designed for local use. Deployments on a shared network should add authentication, TLS, and client rate limits at a reverse proxy, and provision temporary storage and memory per worker.

Browser requests with an `Origin` header must match the service origin. The
loopback command rejects foreign Host headers. Custom ASGI deployments should
enforce their allowed hosts at the reverse proxy or with ASGI middleware.
