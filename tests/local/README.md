# Local reference tests

`tests/test_local_reference.py` compares cutter with the files of a real project cut with an earlier version of the
pipeline. Real projects cannot be published, so the test reads a manifest with paths only:
`tests/local/reference.json` (git-ignored, like everything in this folder except this README).

Without the manifest the tests are skipped. The keys are described in [docs/development.md](../../docs/development.md#local-reference-tests);
missing keys skip the matching test. The referenced files are only read; all output goes to a temporary folder.

Inside the Docker image, mount the referenced folders read-only at the same paths and the manifest at
`/opt/cutter/tests/local/reference.json`.
