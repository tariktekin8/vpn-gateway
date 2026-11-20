# Build context: docker/
FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates openconnect vpnc-scripts iproute2 iptables iputils-ping curl \
    && rm -rf /var/lib/apt/lists/*
COPY common/hub-link.sh /usr/local/bin/hub-link.sh
COPY openconnect/entrypoint.sh /entrypoint.sh
# A Windows checkout has no executable bit.
RUN chmod 755 /entrypoint.sh /usr/local/bin/hub-link.sh
ENTRYPOINT ["/entrypoint.sh"]
