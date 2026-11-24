# Python API reference

## Buffer and I/O

| Public symbol | Behavior |
| --- | --- |
| `Audio(samples, sample_rate)` | Accepts a one-dimensional mono array or a two-dimensional frames-by-channels array. Owns a read-only float64 copy and exposes `samples`, `sample_rate`, `frames`, `channels`, and `duration`. |
| `load_audio(source, *, max_samples=None)` | Reads a path or a seekable binary stream. The optional limit counts scalar samples across all channels. |
| `write_audio(destination, audio, *, subtype=None, format=None)` | Writes a path atomically or writes to a seekable binary stream with an explicit format. WAV defaults to DOUBLE. Integer encodings reject values that would saturate. |

Audio accepts 1–32 channels, sample rates of 1–384000 Hz and finite real samples with magnitude at most 1,000,000. The library raises `ValidationError` for invalid parameters or buffers, `AudioLimitError` for exceeded decode limits, and `AudioIOError` for file and codec errors. All inherit from `AudioError`.

## Analysis and spectrum

- `analysis.analyze(audio, config=AnalysisConfig())` returns `AnalysisResult` with scalar levels, dynamics, clipping, silence, offsets, stereo correlation and a `LoudnessResult`.
- `loudness.measure_loudness(audio)` returns integrated LUFS, maximum momentary and short-term LUFS, and LRA. `loudness.loudness_history(audio)` gives timestamped momentary and short-term levels. `loudness.integrated_loudness(audio)` and `loudness.true_peak(audio)` provide individual measurements. The latter returns linear amplitude.
- `spectral.fft(audio)` returns one-sided DFT coefficients. `spectral.stft(audio, n_fft=1024, hop=512)` returns complex frames and axes. `spectral.spectrogram(audio, n_fft=1024, hop=512)` returns channel-averaged bin power.
- `spectral.spectral_summary(audio, n_fft=2048, rolloff=0.85)` returns centroid, bandwidth, rolloff and fractions of energy per band. `spectral.transients(audio, n_fft=1024, hop=256)` returns spectral flux and candidate onset times.

## DSP and presets

Every function in `openaudiokit.dsp` takes `Audio` first and returns `Audio`. For use in a preset, the table gives the operation key and its keyword arguments. Parameters without defaults are required.

| Operation | Keyword arguments |
| --- | --- |
| `gain` | `db` |
| `invert_polarity` | None |
| `normalize_peak` | `target_dbfs=-1` |
| `normalize_loudness` | `target_lufs=-23`, `ceiling_dbtp=-1` |
| `filter` (Python `filter_audio`) | `kind`, `frequency`, `order=4`, `q=30`. Use a pair for bandpass. |
| `parametric_eq` | `frequency_hz`, `db`, `q=1` |
| `compress` | `threshold_dbfs=-18`, `ratio=4`, `attack_ms=10`, `release_ms=100`, `makeup_db=0` |
| `limit` | `ceiling_dbfs=-1`, `release_ms=50` |
| `noise_gate` | `threshold_dbfs=-45`, `range_db=60`, `attack_ms=5`, `release_ms=75` |
| `fade` | `fade_in_seconds=0`, `fade_out_seconds=0` |
| `to_mono`, `to_stereo`, `encode_mid_side`, `decode_mid_side` | None |
| `mid_side` (Python `process_mid_side`) | `mid_db=0`, `side_db=0` |
| `resample` | `sample_rate` |

## QC and workflows

- `qc.inspect(audio, config=QCConfig())` returns `QCReport`. The configuration has independent limits for clipping level, estimated true peak, DC offset, low-level time, stereo correlation and expected sample rate.
- `qc.compare(reference, candidate, config=QCConfig())` returns both reports and candidate-minus-reference deltas for comparable scalars and bands.
- `report.render_report(data, format="json")` and `report.write_report(destination, data, format=None)` accept the QC, comparison and batch results.
- `batch.load_preset(path)` parses an ordered JSON chain. `batch.process_audio(audio, operations, *, max_samples=None)` applies that chain. `batch.run_batch(sources, *, operations=(), output_dir=None, config=QCConfig(), progress=None, max_samples=None)` returns an ordered list of results and optionally calls the supplied progress function after each file. The sample budget counts scalar input and intermediate samples. Resampling and mono-to-stereo expansion check their output sizes before allocating.
