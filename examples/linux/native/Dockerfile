FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
COPY tools/debian.sources /etc/apt/sources.list.d/debian.sources
COPY tools/apt-retries.conf /etc/apt/apt.conf.d/80-sdk-downloads
COPY tools/apt_packages.py tools/apt-retries.conf /opt/build-tools/
RUN apt-get update && \
    python /opt/build-tools/apt_packages.py --record /opt/native-build-inputs/debian-packages.json install \
      cmake make clang libc++-dev libc++abi-dev libssl-dev openssl && \
    mkdir -p /opt/native-build-inputs && \
    cp /etc/apt/sources.list.d/debian.sources /opt/native-build-inputs/ && \
    cp /etc/apt/apt.conf.d/80-sdk-downloads /opt/native-build-inputs/apt-retries.conf && \
    cp /opt/build-tools/apt_packages.py /opt/native-build-inputs/ && \
    cp /var/lib/apt/lists/*_InRelease /opt/native-build-inputs/ && \
    dpkg-query -W '-f=${binary:Package}\t${Version}\t${Architecture}\n' > /opt/native-build-inputs/system-packages.txt && \
    rm -rf /var/lib/apt/lists/*
COPY examples/linux/native/Dockerfile /opt/native-build-inputs/Dockerfile
COPY . /opt/sdk
WORKDIR /opt/sdk
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "/opt/sdk/tools/package_runtime.py"]
