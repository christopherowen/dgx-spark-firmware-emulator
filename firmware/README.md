# Local firmware inputs

Place locally obtained firmware here. Everything except this README is ignored.
The repository intentionally ships no NVIDIA or MediaTek firmware images.

For each component, keep a local note of:

- Original vendor URL or owned-device acquisition method.
- Board model and firmware component name/revision.
- Original filename, size, and SHA-256.
- Capsule/container versus extracted executable relationship.
- Extraction tool version and exact command, if applicable.

Use `python3 tools/spark_fw.py inspect PATH` to generate outer-header metadata.
It does not inspect nested payloads or verify signatures. Unknown data is left
unknown; do not infer boot addresses or an instruction set from a filename.

Raw hardware captures belong in ignored `captures/`. Before sharing derived
evidence, review it for serial numbers, MAC addresses, device identity,
credentials, and redistribution rights. Never assume that a downloadable
firmware image is licensed for inclusion in a public repository.
