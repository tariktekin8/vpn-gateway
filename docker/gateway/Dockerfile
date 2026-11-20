# Build context: docker/
FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN apt-get update && apt-get install -y --no-install-recommends \
      wireguard-tools iproute2 iptables iputils-ping socat \
    && rm -rf /var/lib/apt/lists/*
COPY gateway/entrypoint.sh /entrypoint.sh
COPY gateway/saml-forward.sh /usr/local/bin/saml-forward.sh
# A Windows checkout has no executable bit.
RUN chmod 755 /entrypoint.sh /usr/local/bin/saml-forward.sh
ENTRYPOINT ["/entrypoint.sh"]
