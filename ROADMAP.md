# Roadmap

## Measurement validation

Run the full EBU loudness test corpus and a reference true-peak corpus. Publish errors and tolerances for each vector. Add an explicit channel layout model before extending loudness measurement to surround.

## Processing and desktop

- Extend bounded-memory processing from measurement work buffers to end-to-end streaming input, DSP and output.
- Evaluate sample-accurate monitoring, a realtime DSP graph and a lookahead true-peak limiter independently of the rendered preview.
- Test audio hardware across Windows, macOS and Linux, including device changes and codec availability.
- Evaluate Windows packaging with PySide6 or separately licensed Qt bindings before bundling a runtime.
