# Measurement audit for 0.2.0

## Reproduce

```sh
python -m pytest tests/test_analysis.py tests/test_dsp.py tests/test_spectral.py tests/test_scientific_audit.py -W error
```

| Area | Reference and signal |
| --- | --- |
| Time-domain levels | Closed-form sine RMS, sample peak, crest factor and opposite-polarity stereo. |
| Programme loudness | De Man K-weighting in pyloudnorm at 44.1, 48 and 96 kHz, with a tolerance of 0.01 LU. Synthetic calibration tones, gated quiet sections, momentary timing and synthetic loudness steps exercise the other outputs. |
| K-weighting block boundaries | The same biquads run once over a continuous stereo buffer using SciPy. The comparison allows four double-precision machine epsilons of relative error for rounding in channel summation. |
| True peak | Quarter-rate sine with known intersample amplitude and a short edge taper. The implementation estimates intersample peaks by interpolating at 4× the sample rate. |
| Filters and EQ | Long sinusoidal tests check rejection, Butterworth amplitude of 1/√2 at cutoff and peaking EQ gain at its centre frequency. |
| Dynamics | Step signals check the compressor's exponential attack and release. Separate signals check the sample limiter ceiling and gate attenuation. |
| Resampling | A 1 kHz passband tone and a 15 kHz stopband tone test 48 kHz to 16 kHz conversion. |
| Signal and input limits | Synthetic silence, near-zero amplitudes, invalid sample rates, NaN, infinity, extreme preset values and bounded decode. |

The official EBU corpus is available under its own terms. The EBU publication server returned HTTP 403 during this audit. The repository ships generated signals. Full ITU-R BS.1770 and EBU R 128 conformance testing remains open. [EBU test set and terms](https://tech.ebu.ch/publications/ebu_loudness_test_set) · [ITU-R BS.1770](https://www.itu.int/rec/R-REC-BS.1770)

## Why an MP3 can decode above 0 dBFS

The local test generated one second of a 997 Hz square wave at 48 kHz, with sample magnitudes bounded by 0.98. libsndfile 1.2.2 encoded it to MP3 and decoded the result into floating point. The maximum decoded sample was +1.3829348 and the minimum was −1.3974836. There were 9,336 samples above +1 and 9,334 below −1. Exact values can vary with encoder and decoder builds.

Lossy coding changes the reconstructed waveform. A decoded sample above +1 is measurable evidence of nominal full-scale overshoot in that decoding path. It cannot establish whether the input was clipped before encoding or whether another decoder clipped during reconstruction. Integer output and a saturating playback path can clip values above their respective limits.

`above_nominal_samples` counts strictly greater than 1 in absolute amplitude. `clipping_samples` counts values at or above the configurable threshold and retains its existing name for API compatibility. MP3 input carries format metadata and reports `lossy_peak_threshold` instead of a generic clipping-threshold issue. Confirming historical clipping requires additional evidence, such as the pre-encode source or a known saturating stage. A true-peak ceiling warning remains independent of both counts.

## Memory behaviour

SoundFile now transfers its decoded float64 array directly into an Audio buffer. Sample validation and full-scale counts scan blocks. K-weighting preserves the IIR state across blocks and allocates one scalar power per frame for the loudness window calculations. True-peak interpolation and spectral summary already use chunks. Complete in-memory source buffers, full STFT output and offline DSP renders still scale with recording length.
