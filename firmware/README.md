# Local firmware inputs

Place locally obtained firmware here. Everything except this README is ignored.
The repository intentionally ships no NVIDIA or MediaTek firmware images.
Firmware must not be redistributed through this project, including in GitHub
releases, issue attachments, or CI artifacts.

`tools/download_firmware.py` stores upstream downloads and their source/checksum
records in `firmware/downloads/`. Provide the direct upstream HTTPS URL and
expected SHA-256; the script verifies both local paths are ignored and untracked
before downloading, and only retains checksum-verified bytes. The destination
is fixed relative to this checkout even when the script is run elsewhere.

```sh
python3 tools/download_firmware.py "$FIRMWARE_URL" \
  --sha256 "$FIRMWARE_SHA256" --filename spark-soc.cab
```

`python3 tools/check_publication.py` checks the Git index for local artifacts
and common firmware containers, including files force-added past `.gitignore`.
CI runs this check. It cannot recognize every possible renamed/compressed blob;
keep every vendor image and extracted payload under this ignored directory.

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
