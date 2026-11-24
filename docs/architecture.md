# Architecture and implementation plan

1. Establish a validated audio object, loss-aware file I/O and synthetic fixtures.
2. Implement measurements, spectral analysis and DSP independently. Verify known signals and compare integrated loudness with a reference meter.
3. Build QC, comparison, report serialization and preset-driven batch processing on top of the core.
4. Wire up the CLI, desktop interface and REST adapter. Keep optional dependencies out of the core.
5. Run the numerical and integration tests, check typing, lint and documentation, build and validate both distribution formats.

The in-memory convention is float64 with shape `(frames, channels)`. Time axes use seconds, frequencies use hertz and level names carry units. The Python core performs no network calls and imports no UI frameworks. Data flows from SoundFile to the audio model, then into pure analysis or processing functions. File outputs are staged beside their destination for atomic replacement.

File-based workflows load complete recordings into memory. Limits on decoded data are exposed by the reader and used by the REST adapter. The interfaces share the same QC and comparison functions. Reports serialize typed measurements along with machine-readable issue codes.
