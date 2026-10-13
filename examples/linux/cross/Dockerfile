FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG TARGET
ENV OPENAVC_CROSS_TARGET=${TARGET}
COPY tools/debian.sources /etc/apt/sources.list.d/debian.sources
RUN set -eu; \
    case "$TARGET" in \
      arm64) arch=arm64; compiler=aarch64-linux-gnu ;; \
      x86_64) arch=amd64; compiler=x86-64-linux-gnu ;; \
      *) echo 'Select arm64 or x86_64.' >&2; exit 1 ;; \
    esac; \
    apt-get update; \
    if [ "$(dpkg --print-architecture)" = "$arch" ]; then \
      apt-get install -y --no-install-recommends cmake make gcc g++; \
    else \
      apt-get install -y --no-install-recommends cmake make "gcc-$compiler" "g++-$compiler"; \
      dpkg --add-architecture "$arch"; apt-get update; \
    fi; \
    mkdir -p /opt/sysroot /tmp/sdk-target-packages; cd /tmp/sdk-target-packages; \
    apt-get download "libc6:$arch" "libc6-dev:$arch" "linux-libc-dev:$arch" \
      "libssl3:$arch" "libssl-dev:$arch" "libgcc-s1:$arch" "libstdc++6:$arch"; \
    for package in ./*.deb; do dpkg-deb -x "$package" /opt/sysroot; done; \
    dpkg-query -W > /opt/build-packages.txt; \
    sha256sum ./*.deb > /opt/target-packages.sha256; \
    mkdir -p /opt/system-inputs; \
    cp /etc/apt/sources.list.d/debian.sources /opt/system-inputs/; \
    cp /var/lib/apt/lists/*_InRelease /opt/system-inputs/; \
    rm -rf /var/lib/apt/lists/* /tmp/sdk-target-packages
WORKDIR /opt/sdk
COPY . .
RUN python examples/linux/cross/build.py
CMD ["python", "-c", "print('Cross-build outputs are in /output.')"]
