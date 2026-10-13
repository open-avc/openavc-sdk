FROM node:22-bookworm@sha256:0e5f906573693feaa1e21057ebdcfdb5bd5021f050b2dc7c9deceb629c7da2a8 AS server
ENV NODE_OPTIONS=--max-old-space-size=3072
WORKDIR /source
COPY sdk/contract/origin.json /tmp/origin.json
RUN git init && git remote add origin https://github.com/open-avc/openavc.git && \
    git fetch --depth 1 origin "$(node -p 'require("/tmp/origin.json").core_head')" && git checkout --detach FETCH_HEAD
COPY tools/build_browser.mjs tools/browser_inventory.mjs /build-tools/
RUN npm --prefix openavc/web/programmer ci && \
    node /build-tools/build_browser.mjs /source/openavc/web/programmer /browser-builds/programmer && \
    npm --prefix openavc/web/simulator ci && \
    node /build-tools/build_browser.mjs /source/openavc/web/simulator /browser-builds/simulator
RUN rm -rf .git openavc/web/programmer/node_modules openavc/web/simulator/node_modules

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS server-runtime
RUN apt-get update && apt-get install -y --no-install-recommends cmake build-essential libssl-dev openssl iputils-ping && rm -rf /var/lib/apt/lists/*
COPY tools/requirements-server.txt tools/requirements-build.txt /opt/build-inputs/
RUN python -m pip download --only-binary=:all: --require-hashes \
    -r /opt/build-inputs/requirements-server.txt -d /opt/server-wheels && \
    python -m pip install --no-index --find-links=/opt/server-wheels --require-hashes \
    -r /opt/build-inputs/requirements-server.txt && \
    python -m pip install --only-binary=:all: --require-hashes \
    -r /opt/build-inputs/requirements-build.txt -r /opt/build-inputs/requirements-server.txt
COPY --from=server /source /opt/openavc
COPY --from=server /browser-builds /opt/browser-builds
RUN python -m pip install --no-index --no-deps --no-build-isolation /opt/openavc && python -m pip check
COPY . /opt/sdk
RUN python -m pip install --no-index --no-deps --no-build-isolation /opt/sdk && python -m pip check
WORKDIR /opt/sdk
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python"]

FROM server-runtime AS example
RUN cmake -S /opt/sdk -B /opt/sdk/build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build /opt/sdk/build --target example-c openavc-device-service --parallel 2
CMD ["examples/system/product.py"]
