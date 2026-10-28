# Firmware storage and publication

The first emulation target is DGX Spark SoC boot firmware / UEFI.

Vendor firmware must not be redistributed. Keep downloaded images, extracted
payloads, and their local metadata under the Git-ignored `firmware/` directory.
The downloader's fixed destination is `firmware/downloads/`. Never force-add
these files or attach them to releases, issues, or CI artifacts. Publish only
project code, documentation, synthetic fixtures, and upstream references.

Before committing or pushing, run:

```sh
python3 -m unittest discover -s tests -v
python3 tools/check_publication.py
```

The publication check inspects the Git index; stage intended source changes
before running it. Do not relax the ignore rules to include vendor firmware.
