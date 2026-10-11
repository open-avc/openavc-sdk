FROM python:3.12-slim-bookworm
ARG TARGET
ENV OPENAVC_CROSS_TARGET=${TARGET}
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
    rm -rf /var/lib/apt/lists/* /tmp/sdk-target-packages
WORKDIR /opt/sdk
COPY . .
RUN python examples/linux/cross/build.py
CMD ["python", "-c", "print('Cross-build outputs are in /output.')"]
