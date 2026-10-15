#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
wheel_dir=${1:-"$script_dir/wheels"}
mkdir -p "$wheel_dir"

python3 -m pip download \
  --dest "$wheel_dir" \
  --platform manylinux_2_28_aarch64 \
  --platform manylinux2014_aarch64 \
  --python-version 3.10 \
  --implementation cp \
  --abi cp310 \
  --only-binary=:all: \
  --no-deps \
  --require-hashes \
  -r "$script_dir/requirements.lock"

printf 'Downloaded hash-verified CPython 3.10 / Linux aarch64 wheels to %s\n' "$wheel_dir"
