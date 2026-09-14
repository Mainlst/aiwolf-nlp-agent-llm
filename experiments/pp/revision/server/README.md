# Historical server source

The public upstream is [aiwolfdial/aiwolf-nlp-server](https://github.com/aiwolfdial/aiwolf-nlp-server).
The experiment used local commit `f73361c9f0f42ca433c72fd27420d032f2e48df8`, one commit after the publicly retrievable base
[`4088f58da4ab2bef427c41cc7a04a13c2ffe3da5`](https://github.com/aiwolfdial/aiwolf-nlp-server/commit/4088f58da4ab2bef427c41cc7a04a13c2ffe3da5).
An unauthenticated GitHub check on 2026-09-14 returned the base commit and its matching Git tree;
the historical commit endpoint returned `422: No commit found for SHA`.
The experiment's seat-order change must therefore be supplied with the release instead of assuming that a clone of upstream contains it.

`source/` preserves all Go files outside the integration-test directory `test/`, plus `go.mod`, `go.sum`, and the original MIT [license](source/LICENSE).
Each file is copied byte-for-byte from the historical Git commit. `source-manifest.json` records Git blob identifiers and SHA-256 hashes.
`seat-assignment.patch` is the complete five-file change from the public base to the historical commit.
The patch orders optimized-match connections by the frozen team index and passes that order into game/agent creation; its unit test checks the resulting team and role order.
The unmodified public archive was downloaded, its 69 corresponding snapshot files matched against the local base, and the patch applied cleanly.
All 70 patched files then matched `source/`; see `public-source-verification.json`.
Configuration templates and other upstream documentation remain available from the linked base; the study's five-player example is `config/server.example.yml` at the code release root.

## Verify and build

The source check needs only Python 3.11 or newer and makes no network request:

```bash
python3 -B experiments/pp/revision/server/verify_source.py
```

On Linux amd64, install Go 1.25.0 and a C compiler, then run from the code release root:

```bash
bash experiments/pp/revision/server/build.sh
```

`SERVER_GO` may point to the Go executable. Build caches stay under `.verification/server-build` unless `SERVER_BUILD_ROOT` is set.
The helper fetches pinned dependencies from the public Go module proxy when absent, checks them using `go.sum`/the Go checksum database,
runs only `TestOrderedRoleConnectionsUsesOptimizerSeats`, and builds `server/aiwolf-nlp-server-linux-amd64-seatfixed-rebuilt`.
It does not start a server, read credentials, call an LLM, or run the server-starting integration tests.
It refuses an existing output; an optional first argument selects a new output path.

The 2026-09-14 verification passed that unit test and built SHA-256
`aebf4aebd04fa3bda0e8e814a1d0a64c7b19bc751050a8d59cbe2b3c3f2dccc8`.
[build-verification.json](build-verification.json) records the verified official Go archive checksum,
compiler and platform settings, source-manifest hash, unit-test output, and the inspected build information for both executables.
The rebuilt executable is also excluded from the distribution; run the recipe to create a local build.

The historical binary SHA-256 is `41f3f0d79b49e4419e2c2831ba1849e6373443d9ee36f7eca993bb1713b901b4`.
Inspection of that retained binary confirmed Go 1.25.0, Linux amd64, CGO enabled,
`vcs.revision=f73361c9f0f42ca433c72fd27420d032f2e48df8`, and `vcs.modified=false`.
Those records connect the binary to the preserved source; the binary itself is not distributed.
This portable recipe uses `-trimpath -buildvcs=false`, so its binary is a new build and is not expected to share the historical hash.
The existing experimental specs require the historical hash and will reject a different build.
For a new study, record the rebuilt binary hash in a new spec, preserve its build environment and seat-order test result alongside the run, and report the changed executable as a new execution condition.
The [new-study instructions](../../../../docs/new-experiments.md) show how to set the spec's required hash and select the rebuilt executable.
Do not change the historical specs or present a new build as the executable used for the reported games.

The source check and seat-order unit test establish source integrity and that ordering invariant.
They do not test end-to-end game behavior, guarantee a byte-identical executable, or reproduce model generations.
