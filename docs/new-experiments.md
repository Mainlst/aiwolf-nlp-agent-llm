# Generating new outputs

Offline reproduction is the supported release verification path. New games require a separate full environment (`uv sync --frozen`), provider credentials, a configured five-player server, time/cost budgets, and a **new** run directory. No new model calls or games were performed during release preparation. The paper used gpt-5.6-luna at temperature 1.0 and gpt-5.6-sol for semantic judgments. Saved outputs are authoritative; rerunning a model does not guarantee identical generations.

The public server repository is [aiwolfdial/aiwolf-nlp-server](https://github.com/aiwolfdial/aiwolf-nlp-server). The experiment used local seat-fixed commit `f73361c9f0f42ca433c72fd27420d032f2e48df8`, with Go 1.25.0, on top of public base `4088f58da4ab2bef427c41cc7a04a13c2ffe3da5`. The historical commit is not retrievable from the public repository as of 2026-09-14. The release now includes its [preserved source, complete patch, license and build recipe](../experiments/pp/revision/server/README.md). Public-base files plus the patch were checked against all 70 preserved files; a Go 1.25.0 build and the seat-order unit test succeeded without starting a game server.

The historical binary SHA-256 is `41f3f0d79b49e4419e2c2831ba1849e6373443d9ee36f7eca993bb1713b901b4`. Its recorded Go build information identifies the same historical commit and a clean tree. The binary itself is not distributed. The portable build uses `-trimpath -buildvcs=false` and produced `aebf4aebd04fa3bda0e8e814a1d0a64c7b19bc751050a8d59cbe2b3c3f2dccc8` in this verification environment. This is a new executable, with no claim of byte identity or end-to-end game equivalence; the source/build records distinguish it from the historical binary.

The historical default server location is `server/aiwolf-nlp-server-linux-amd64-seatfixed`; override it with `--server-binary` or `REVISION_SERVER_BINARY`. The example server settings are in `config/server.example.yml`. Original protocol/source snapshots are in the raw archives. Keep all historical specs and their binary hash unchanged. A rebuilt server needs a separate new-study spec containing its actual hash, because preparation and preflight reject mismatched executables.

Before starting a new study, copy and edit the revision spec's deadlines and budgets, set a unique `REVISION_TAG`, and point `REVISION_SPEC` at that new spec. Existing dated specs document the historical experiment and are not a fresh run schedule. The helper defaults to the new tag `new-study`, never the completed paper run ID.

After installing Go 1.25.0 and a C compiler on Linux amd64, build the server and create a new spec with the actual rebuilt hash:

```bash
bash experiments/pp/revision/server/build.sh
.venv/bin/python3 - <<'PY'
import hashlib
from pathlib import Path
import yaml

spec_path = Path('new-study.yml')
if spec_path.exists():
    raise FileExistsError(spec_path)
spec = yaml.safe_load(Path('experiments/pp/revision/spec-resume8.yml').read_text())
binary = Path('server/aiwolf-nlp-server-linux-amd64-seatfixed-rebuilt')
spec['normal']['required_server_binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
PY
```

Before preparation, edit `new-study.yml`: set timezone-aware future `normal.start_deadline`, `normal.data_deadline`, and `fixed.data_deadline` (the two data deadlines must agree), along with the new study's budgets and sample sizes. Keep its binary hash tied to the executable just built. Then select the new spec and binary explicitly:

```bash
export REVISION_TAG=my-new-study
export REVISION_SPEC="$PWD/new-study.yml"
export REVISION_SERVER_BINARY="$PWD/server/aiwolf-nlp-server-linux-amd64-seatfixed-rebuilt"
bash experiments/pp/revision/commands.sh prepare-smoke-b
bash experiments/pp/revision/commands.sh check-smoke-b
```

Preserve the actual binary hash, `go version -m` output, compiler version and seat-order test output with the new run; the release's [build-verification record](../experiments/pp/revision/server/build-verification.json) documents the release-preparation build only. Report the changed executable as a new execution condition. These preparation commands do not start games. The build's single seat-order unit test does not establish complete live-game behavior, and new model outputs remain stochastic.

```bash
.venv/bin/python3 -m experiments.pp.revision.prepare --help
bash experiments/pp/revision/commands.sh help
```

Preparation and preflight do not generate outputs. `execute-*` and `judge-*` explicitly invoke live models; `config/.env` is read only for such execution and is not included in the repository. Never resume an archived completed run. Preserve its snapshots when preparing a new run, and report new results separately from the four published evaluations.
