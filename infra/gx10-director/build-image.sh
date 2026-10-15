#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
base_image=${BASE_IMAGE:-comfyui:local}
expected_id=${EXPECTED_BASE_IMAGE_ID:?set the inspected comfyui:local image ID}
image_tag=${IMAGE_TAG:-comfyui:gx10-director}
python_executable=${PYTHON:-/usr/bin/python3}
expected_torch=${EXPECTED_TORCH:-2.14.1+cu130}
expected_torchvision=${EXPECTED_TORCHVISION:-0.29.1+cu130}

actual_id=$(docker image inspect --format '{{.Id}}' "$base_image")
if [ "$actual_id" != "$expected_id" ]; then
  printf 'Base image changed: expected %s, found %s\n' "$expected_id" "$actual_id" >&2
  exit 1
fi

actual_arch=$(docker image inspect --format '{{.Architecture}}' "$base_image")
if [ "$actual_arch" != arm64 ]; then
  printf 'Base image must be arm64; found %s\n' "$actual_arch" >&2
  exit 1
fi

comfyui_user=$(docker image inspect --format '{{.Config.User}}' "$base_image")
comfyui_user=${comfyui_user:-root}
docker build --platform linux/arm64 \
  --build-arg "BASE_IMAGE=$base_image" \
  --build-arg "PYTHON=$python_executable" \
  --build-arg "COMFYUI_USER=$comfyui_user" \
  --build-arg "EXPECTED_TORCH=$expected_torch" \
  --build-arg "EXPECTED_TORCHVISION=$expected_torchvision" \
  --tag "$image_tag" \
  --file "$script_dir/Dockerfile" \
  "$script_dir"

printf 'Built %s from %s (%s)\n' "$image_tag" "$base_image" "$actual_id"
