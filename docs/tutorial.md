# Check and process a recording

Generate a synthetic WAV from the root of the checkout.

```sh
python examples/generate_tone.py demo.wav
openaudiokit report demo.wav qc.html --expected-sample-rate 48000
```

Open `qc.html` and look for the issue codes. Clipping counts individual sample values over the threshold. Negative stereo correlation flags potential mono cancellation. The silence check counts the total duration of low-level windows, which may be separated in time. Thresholds appear in the same report.

## Apply a preset

```json
{
  "operations": [
    {"name": "filter", "params": {"kind": "highpass", "frequency": 35}},
    {"name": "fade", "params": {"fade_in_seconds": 0.02, "fade_out_seconds": 0.08}},
    {"name": "normalize_peak", "params": {"target_dbfs": -1}}
  ]
}
```

Use this preset with `openaudiokit process demo.wav output.wav --preset examples/cleanup.json`, then run `openaudiokit compare demo.wav output.wav`. Deltas equal candidate minus reference. Band energy is a fraction, so a change of 0.03 represents three percentage points. Spectral deltas require equal sample rates. Loudness deltas require equal channel counts.

For multiple files, run `openaudiokit batch a.wav b.wav --preset examples/cleanup.json --output-dir output -o batch.csv -v`. Invoke the command with an explicit list of input paths. A batch without a preset or output directory analyzes each file. With an output directory, the writer appends `.wav` to each input filename and can replace earlier outputs at those locations. The batch rejects input/output path conflicts and duplicate destination names, including names that differ only by case or Unicode normalization. A failed item records an error and the other items continue. The command exits with status 1 if any item fails.

JSON uses `null` for unavailable values. CSV describes nested fields using dots and array indices, with two columns named `field` and `value`. HTML is standalone. Every path write stages the new contents next to the destination before replacement.
