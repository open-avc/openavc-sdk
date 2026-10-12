FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 iptables && rm -rf /var/lib/apt/lists/*
CMD ["iptables", "--version"]
