#!/usr/bin/env bash
# Build and run only the seat-order unit test; never launch the game server.
set -euo pipefail
server_bundle="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
release_root="$(cd "$server_bundle/../../../.." && pwd)"
server_go="${SERVER_GO:-go}"
server_go="$(command -v "$server_go")"
if [[ "$server_go" != /* ]]; then server_go="$PWD/$server_go"; fi
server_build_root="${SERVER_BUILD_ROOT:-$release_root/.verification/server-build}"
server_output="${1:-$release_root/server/aiwolf-nlp-server-linux-amd64-seatfixed-rebuilt}"
if [[ "$server_output" != /* ]]; then server_output="$PWD/$server_output"; fi
if [[ "$server_build_root" != /* ]]; then server_build_root="$PWD/$server_build_root"; fi
if [[ -e "$server_output" ]]; then
    printf 'Refusing existing output: %s\n' "$server_output" >&2
    exit 1
fi
server_version="$(GOTOOLCHAIN=local GOENV=off "$server_go" version)"
if [[ "$server_version" != 'go version go1.25.0 linux/amd64' ]]; then
    printf 'This historical build recipe requires Go 1.25.0 on linux/amd64. Found: %s\n' "$server_version" >&2
    exit 1
fi
python3 -B "$server_bundle/verify_source.py"
mkdir -p "$server_build_root/cache" "$server_build_root/modules" "$server_build_root/tmp" "$(dirname "$server_output")"
export GOCACHE="$server_build_root/cache" GOMODCACHE="$server_build_root/modules"
export GOPATH="$server_build_root/gopath" GOTMPDIR="$server_build_root/tmp"
export GOTOOLCHAIN=local GOENV=off GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org
export GOOS=linux GOARCH=amd64 GOAMD64=v1 CGO_ENABLED=1
cd "$server_bundle/source"
"$server_go" test -mod=readonly ./util -run '^TestOrderedRoleConnectionsUsesOptimizerSeats$' -count=1
"$server_go" build -mod=readonly -trimpath -buildvcs=false -o "$server_output" .
sha256sum "$server_output"
printf 'Rebuilt source; this output is not asserted to match the historical binary hash.\n'
