FROM node:22-bookworm AS server
ENV NODE_OPTIONS=--max-old-space-size=3072
WORKDIR /source
COPY sdk/contract/origin.json /tmp/origin.json
RUN git init && git remote add origin https://github.com/open-avc/openavc.git && \
    git fetch --depth 1 origin "$(node -p 'require("/tmp/origin.json").core_head')" && git checkout --detach FETCH_HEAD
RUN npm --prefix openavc/web/programmer ci && npm --prefix openavc/web/programmer run build && \
    npm --prefix openavc/web/simulator ci && npm --prefix openavc/web/simulator run build
RUN rm -rf .git openavc/web/programmer/node_modules openavc/web/simulator/node_modules

FROM python:3.12-slim-bookworm AS server-runtime
RUN apt-get update && apt-get install -y --no-install-recommends cmake build-essential libssl-dev openssl iputils-ping && rm -rf /var/lib/apt/lists/*
COPY --from=server /source /opt/openavc
RUN python -m pip install --no-cache-dir /opt/openavc
COPY . /opt/sdk
RUN python -m pip install --no-cache-dir /opt/sdk
WORKDIR /opt/sdk
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python"]

FROM server-runtime AS example
RUN cmake -S /opt/sdk -B /opt/sdk/build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build /opt/sdk/build --target example-c example-service --parallel 2
CMD ["examples/system/product.py"]
