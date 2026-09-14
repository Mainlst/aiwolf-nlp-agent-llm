#!/usr/bin/env bash
# Run from any directory. No default action starts games or calls an API.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
revision_python="${REVISION_PYTHON:-$PWD/.venv/bin/python3}"
revision_root="${REVISION_RUN_ROOT:-$PWD/experiments/runs}"
revision_tag="${REVISION_TAG:-new-study}"
revision_spec="${REVISION_SPEC:-$PWD/experiments/pp/revision/spec-resume8.yml}"
revision_server_binary="${REVISION_SERVER_BINARY:-$PWD/server/aiwolf-nlp-server-linux-amd64-seatfixed}"
b_run="$revision_root/pp-revision-B-$revision_tag"
a_run="$revision_root/pp-revision-A-$revision_tag"
b_smoke="$revision_root/pp-revision-B-smoke-$revision_tag"
a_smoke="$revision_root/pp-revision-A-smoke-$revision_tag"
case "${1:-help}" in
  prepare-b)
    "$revision_python" -m experiments.pp.revision.prepare B "$b_run" --spec "$revision_spec" --server-binary "$revision_server_binary" ;;
  prepare-a)
    "$revision_python" -m experiments.pp.revision.prepare A "$a_run" --b-run "$b_run" --spec "$revision_spec" ;;
  prepare-smoke-b)
    "$revision_python" -m experiments.pp.revision.prepare B "$b_smoke" --purpose smoke --spec "$revision_spec" --server-binary "$revision_server_binary" ;;
  prepare-smoke-a)
    "$revision_python" -m experiments.pp.revision.prepare A "$a_smoke" --purpose smoke --spec "$revision_spec" ;;
  check-b|check-a|check-smoke-b|check-smoke-a)
    case "$1" in check-b) run="$b_run";; check-a) run="$a_run";; check-smoke-b) run="$b_smoke";; *) run="$a_smoke";; esac
    "$revision_python" -m experiments.pp.revision.preflight "$run" ;;
  dry-run-b|dry-run-a)
    if [[ "$1" == dry-run-b ]]; then
      "$revision_python" -m experiments.pp.revision.normal "$b_run"
    else
      "$revision_python" -m experiments.pp.revision.fixed "$a_run"
    fi ;;
  execute-b|execute-smoke-b)
    run="$b_run"; [[ "$1" == execute-smoke-b ]] && run="$b_smoke"
    "$revision_python" -m experiments.pp.revision.normal "$run" --execute ;;
  repair-b)
    "$revision_python" -m experiments.pp.revision.normal "$b_run" --repair-only --execute ;;
  execute-a|execute-smoke-a)
    run="$a_run"; [[ "$1" == execute-smoke-a ]] && run="$a_smoke"
    "$revision_python" -m experiments.pp.revision.fixed "$run" --execute ;;
  judge-a|judge-smoke-a)
    run="$a_run"; [[ "$1" == judge-smoke-a ]] && run="$a_smoke"
    "$revision_python" -m experiments.pp.revision.fixed "$run" --judge --execute ;;
  freeze-b|freeze-a|freeze-smoke-b|freeze-smoke-a)
    case "$1" in freeze-b) run="$b_run";; freeze-a) run="$a_run";; freeze-smoke-b) run="$b_smoke";; *) run="$a_smoke";; esac
    "$revision_python" -m experiments.pp.revision.preflight "$run" --freeze ;;
  analyze-b|analyze-a|analyze-smoke-b|analyze-smoke-a)
    case "$1" in analyze-b) run="$b_run";; analyze-a) run="$a_run";; analyze-smoke-b) run="$b_smoke";; *) run="$a_smoke";; esac
    "$revision_python" -m experiments.pp.revision.analyze "$run" ;;
  test)
    "$revision_python" -m unittest discover -s tests/pp/revision -t . -v ;;
  help)
    cat <<'USAGE'
Offline: prepare-{a,b}, prepare-smoke-{a,b}, check-{a,b}, check-smoke-{a,b}, dry-run-{a,b}, test
LIVE API calls: execute-{a,b}, repair-b, execute-smoke-{a,b}, judge-a, judge-smoke-a
After generation/judging: freeze-{a,b}, analyze-{a,b} (also freeze-smoke-* / analyze-smoke-*)
Environment: REVISION_TAG, REVISION_RUN_ROOT, REVISION_SPEC, REVISION_PYTHON (default .venv/bin/python3)
Server: REVISION_SERVER_BINARY (default server/aiwolf-nlp-server-linux-amd64-seatfixed; required hash checked)
Credentials are read only from config/.env at execution. No key is copied to the run.
USAGE
    ;;
  *) printf 'Unknown subcommand: %s\n' "$1" >&2; exit 2 ;;
esac
