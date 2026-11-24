# Measurement algorithms

## Levels, dynamics and stereo

The input uses normalized float samples with a nominal full-scale amplitude of 1. Sample peak is the largest absolute sample value across channels. RMS uses the mean square across all frames and channels. Both levels use 20 log₁₀ of the respective amplitude, referenced to 1. Crest factor is the difference between sample peak and RMS in dB. DC offset is the per-channel sample mean.

The clipping-threshold count includes individual sample values at or above the configured absolute amplitude. A positive maximum PCM code can decode below 1.0, and a previously clipped signal can sit below the threshold. The separate above-nominal count includes values with absolute amplitude strictly above 1.0. The [measurement audit](scientific-audit.md) covers MP3 overshoots and why these counts cannot establish clipping history.

The level envelope takes the largest channel RMS in each nonoverlapping block. Silence is the sum of the lengths of blocks below the configured RMS threshold. A partial final block contributes its actual duration. The dynamic range metric is the 95th minus the 10th percentile of active block levels in dB. The default block duration is 100 ms and the default silence threshold for `analyze()` is −60 dBFS.

Stereo correlation is the Pearson correlation of the two channels after removing their means. In-phase copies yield +1, opposite-polarity copies yield −1, and constant channels have undefined correlation. A file-wide correlation can hide brief changes in phase relationship.

## Programme loudness and true peak

The K-weighting stage uses the two 48 kHz biquads specified in ITU-R BS.1770-5 Annex 1. At other rates the filters are retuned with an inverse bilinear transform followed by prewarping at the pole frequency of each stage. Mono and stereo channel energies are summed with unit channel weights. For integrated loudness, the meter averages 400 ms blocks with 75% overlap. It discards blocks below −70 LUFS, computes the mean power of the remainder, and then applies a second gate 10 LU below that mean's level. Incomplete blocks are discarded. [ITU-R BS.1770-5](https://www.itu.int/rec/R-REC-BS.1770-5-202311-I/en)

Momentary and short-term loudness use complete rectangular 400 ms and 3 s windows without gating. Window ends advance by nominal 10 ms and 100 ms, respectively. `measure_loudness()` returns the maximum of each history and `loudness_history()` returns the ungated values and their window-end times. Histories represent zero power as negative infinity. Scalar reports use `None` for unavailable or unbounded values, serialized as JSON `null`. Loudness requires mono or stereo at 8 kHz or higher. Higher channel counts require explicit channel layout metadata, which the current meter does not accept. [EBU R 128](https://tech.ebu.ch/publications/r128) and [EBU Tech 3341](https://tech.ebu.ch/docs/tech/tech3341.pdf)

Loudness Range uses 3 s blocks at nominal 100 ms intervals, a −70 LUFS absolute gate and a relative gate 20 LU below the absolute-gated mean power. LRA is the difference between the 95th and 10th percentiles of the remaining levels. At least 3 s of input and two gated blocks are required. The file calculation includes 1.5 s of trailing silence in the LRA input. Estimates for short programmes are sensitive to beginning and ending levels. [EBU Tech 3342](https://tech.ebu.ch/docs/tech/tech3342.pdf)

True peak is estimated with 4× polyphase interpolation and a Kaiser-windowed low-pass FIR. The maximum includes original sample peaks and interpolated values across all channels. Chunks use neighbouring samples for filter context, and the signal is extended with zeros outside the recording. A finite interpolation filter can attenuate near-Nyquist content. Peaks can also occur between points on the oversampled grid. [ITU-R BS.1770-5 Annex 2](https://www.itu.int/rec/R-REC-BS.1770-5-202311-I/en) and [SciPy polyphase resampling](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.resample_poly.html)

Validation uses synthetic signals of known amplitude, phase and duration, selected synthesized EBU-style tone steps and integrated loudness comparisons with the De Man filter in pyloudnorm. Broadcast meter certification and full official test-set validation are pending. [pyloudnorm reference implementation](https://github.com/csteinmetz1/pyloudnorm)

## Spectrum and transients

`fft()` computes the unwindowed, unscaled, one-sided DFT on each channel. `stft()` uses a periodic Hann window, centres frames on the hop grid and zero-pads outside the recording. `spectrogram()` averages per-channel powers, so opposite-polarity channels do not cancel. Bin powers are normalized by the transform length and the window's squared norm. Interior positive-frequency bins are doubled to include their negative-frequency counterparts. The resulting scale is mean-square power per bin. A hop greater than the frame length leaves gaps in the recording. [SciPy real FFT](https://docs.scipy.org/doc/scipy/reference/generated/scipy.fft.rfft.html)

`spectral_summary()` accumulates overlapping modified periodograms with a half-sample-shifted Hann window, 2,048 samples per frame by default, and roughly 50% overlap. It includes the end of the recording and pads short clips to one frame. The centroid is the power-weighted mean frequency. Bandwidth is the power-weighted standard deviation around that centroid. Rolloff is the first bin at which cumulative power reaches the chosen fraction, 85% by default. The Welch periodogram method informs this aggregation. [SciPy Welch method](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html)

| Band key | Frequency range |
| --- | --- |
| `sub_bass` | 0–60 Hz |
| `bass` | 60–250 Hz |
| `low_mid` | 250–500 Hz |
| `mid` | 500–2,000 Hz |
| `upper_mid` | 2–4 kHz |
| `presence` | 4–6 kHz |
| `brilliance` | 6–20 kHz |
| `ultrasonic` | 20 kHz and above |

A bin exactly at a boundary belongs to the higher band. Values are fractions of available power. The sample rate sets the Nyquist limit and frequency resolution. A silent signal produces zero fractions and undefined descriptors.

`transients()` computes the Euclidean norm of positive frame-to-frame changes in spectral amplitude. Peak detection uses a median-based threshold, minimum prominence and 50 ms minimum spacing. It suppresses frames centred near the recording boundaries where the frame would be partly padded. Candidate times are hop-resolution estimates and depend on window length. [SciPy peak detection](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.find_peaks.html)

## Processing

The pass filters use a causal Butterworth response implemented as second-order sections. The notch uses a second-order IIR section and the parametric EQ uses the RBJ peaking biquad. Filter state starts at zero. Sample-rate conversion reduces the rational rate ratio and applies a polyphase low-pass FIR with zero extension at the boundaries. [SciPy Butterworth design](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.butter.html) · [Audio EQ Cookbook](https://www.w3.org/TR/audio-eq-cookbook/) · [SciPy polyphase resampling](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.resample_poly.html)

The compressor, gate and limiter share a peak detector across channels. Compressor attack and release smooth a hard-knee gain curve in linear amplitude. The gate uses separate opening and closing time constants. The limiter applies an immediate reduction when a sample exceeds its target and smooths recovery. Its ceiling applies to sample peaks. Very fast gain changes can generate distortion.

Loudness normalization applies one fixed gain based on integrated LUFS, capped by the oversampled peak estimate. The result can fall below the target when the ceiling binds. Signals close to the absolute loudness gate can change gating decisions after scaling. Measure the saved output when delivery tolerances are critical.
