ARG BASE_IMAGE=comfyui:local
FROM ${BASE_IMAGE}

ARG PYTHON=/usr/bin/python3
ARG COMFYUI_USER=root
ARG EXPECTED_TORCH=2.14.1+cu130
ARG EXPECTED_TORCHVISION=0.29.1+cu130
ARG EXPECTED_CUDA=13.0
ARG APT_SNAPSHOT=20261014T000000Z
ARG FFMPEG_VERSION=7:4.4.2-0ubuntu0.22.04.1

USER root

RUN set -eux; \
    printf '%s\n' \
      "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT} jammy main universe" \
      "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT} jammy-updates main universe" \
      "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT} jammy-security main universe" \
      > /tmp/gx10-director.list; \
    apt-get -o Dir::Etc::sourcelist=/tmp/gx10-director.list \
      -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0 update; \
    apt-get -o Dir::Etc::sourcelist=/tmp/gx10-director.list \
      -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0 \
      install -y --no-install-recommends "ffmpeg=${FFMPEG_VERSION}"; \
    test "$(dpkg-query -W -f='${Version}' ffmpeg)" = "${FFMPEG_VERSION}"; \
    command -v ffmpeg; command -v ffprobe; \
    rm -rf /var/lib/apt/lists/* /tmp/gx10-director.list

COPY requirements.lock /opt/gx10-director/requirements.lock
COPY wheels/ /opt/gx10-director/wheels/

RUN set -eux; \
    stack_before="$(${PYTHON} -c 'import torch, torchvision; print(torch.__version__ + "|" + torchvision.__version__ + "|" + str(torch.version.cuda))')"; \
    test "${stack_before}" = "${EXPECTED_TORCH}|${EXPECTED_TORCHVISION}|${EXPECTED_CUDA}"; \
    ${PYTHON} -m pip install --no-index \
      --find-links=/opt/gx10-director/wheels \
      --only-binary=:all: --no-deps --require-hashes \
      -r /opt/gx10-director/requirements.lock; \
    stack_after="$(${PYTHON} -c 'import torch, torchvision; print(torch.__version__ + "|" + torchvision.__version__ + "|" + str(torch.version.cuda))')"; \
    test "${stack_before}" = "${stack_after}"

COPY verify_runtime.py /opt/gx10-director/verify_runtime.py

RUN set -eux; \
    EXPECTED_TORCH="${EXPECTED_TORCH}" \
    EXPECTED_TORCHVISION="${EXPECTED_TORCHVISION}" \
    EXPECTED_CUDA="${EXPECTED_CUDA}" \
      ${PYTHON} /opt/gx10-director/verify_runtime.py

USER ${COMFYUI_USER}
