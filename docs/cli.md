# Command-line reference

Install the `cli` extra. Run `openaudiokit COMMAND --help` for the generated option list.

| Command | Example |
| --- | --- |
| `analyze` | `openaudiokit analyze mix.wav -o measurements.json` |
| `compare` | `openaudiokit compare original.wav candidate.wav -o differences.csv` |
| `normalize` | `openaudiokit normalize mix.wav delivery.wav --lufs -23 --ceiling -1` |
| `process` | `openaudiokit process mix.wav edited.wav --preset examples/cleanup.json` |
| `batch` | `openaudiokit batch A.wav B.wav --preset examples/cleanup.json --output-dir out -o batch.json` |
| `report` | `openaudiokit report mix.wav qc.html --expected-sample-rate 48000` |

Reports without an output path go to stdout, where applicable. Batch progress goes to stderr. A batch exits with status 1 if any input fails. The Python script at `examples/delivery_check.py` applies a QC policy from an integration script.
