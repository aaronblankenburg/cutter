# cutter: tools, FilmCraft's headless CLI and the review app in one image (linux/amd64 and linux/arm64).
# Bump FilmCraft: set FILMCRAFT_VERSION and both SHA256 values from the release's SHA256SUMS.txt
# (https://github.com/storytold/filmcraft/releases), then rebuild and run the tests (docs/development.md).
FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ARG TARGETARCH
ARG FILMCRAFT_VERSION=0.19.0
ARG FILMCRAFT_SHA256_AMD64=76149343c19700ba6ac905fde8a019e0917d06507f426a5938396d97a1ca2124
ARG FILMCRAFT_SHA256_ARM64=7e9e857325be2b6197b57e2a21fcf8faf45fa42644c79eebed1fb734c3f8bed7

ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg, fonts used by titles, subtitles and maps (Noto Sans, P052, Liberation Sans Narrow, DejaVu), ssh/rsync for deploy
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-liberation fonts-noto-core fonts-urw-base35 \
      openssh-client rsync ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# FilmCraft headless CLI from the official release tarball (MIT OR Apache-2.0), checksum verified; only the CLI and
# its notices are installed (the fonts it needs for scripts like Arabic or CJK are built into the binary)
RUN set -eu; \
    case "${TARGETARCH:-amd64}" in \
      amd64) arch=x86_64; sum="$FILMCRAFT_SHA256_AMD64" ;; \
      arm64) arch=aarch64; sum="$FILMCRAFT_SHA256_ARM64" ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    name="filmcraft-${FILMCRAFT_VERSION}-linux-${arch}"; \
    curl -fsSL -o "/tmp/${name}.tar.gz" "https://github.com/storytold/filmcraft/releases/download/v${FILMCRAFT_VERSION}/${name}.tar.gz"; \
    echo "${sum}  /tmp/${name}.tar.gz" | sha256sum -c -; \
    tar -xzf "/tmp/${name}.tar.gz" -C /tmp; \
    install -m 755 "/tmp/${name}/bin/filmcraft-cli" /usr/local/bin/filmcraft-cli; \
    mkdir -p /usr/share/doc/filmcraft; \
    cp /tmp/${name}/share/doc/filmcraft/LICENSE-MIT /tmp/${name}/share/doc/filmcraft/LICENSE-APACHE \
       /tmp/${name}/share/doc/filmcraft/NOTICE /tmp/${name}/share/doc/filmcraft/OFL-*.txt /usr/share/doc/filmcraft/; \
    rm -rf "/tmp/${name}" "/tmp/${name}.tar.gz"; \
    filmcraft-cli --version

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt \
 && pip install --no-deps scenedetect==0.7.2 \
 && rm /tmp/requirements.txt

COPY pyproject.toml README.md LICENSE /opt/cutter/
COPY src /opt/cutter/src
COPY tests /opt/cutter/tests
COPY docker/entrypoint.sh /usr/local/bin/cutter-entrypoint
RUN pip install --no-deps /opt/cutter \
 && mkdir -p /models /home/cutter \
 && chmod 1777 /models /home/cutter \
 && chmod 666 /etc/passwd \
 && chmod 755 /usr/local/bin/cutter-entrypoint

# HF_HOME: Whisper models in the volume cutter-models (also the numba cache of librosa); runs as the caller's UID:GID
ENV HF_HOME=/models HOME=/home/cutter NUMBA_CACHE_DIR=/models/numba-cache MPLCONFIGDIR=/tmp/matplotlib \
    CUTTER_IN_CONTAINER=1 FILMCRAFT_CLI=/usr/local/bin/filmcraft-cli
WORKDIR /home/cutter
ENTRYPOINT ["cutter-entrypoint"]
CMD ["--help"]
