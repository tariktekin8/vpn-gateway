# Build context: docker/
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      wireguard-tools iproute2 iptables iputils-ping socat \
    && rm -rf /var/lib/apt/lists/*
COPY gateway/entrypoint.sh /entrypoint.sh
COPY gateway/saml-forward.sh /usr/local/bin/saml-forward.sh
# A Windows checkout has no executable bit.
RUN chmod 755 /entrypoint.sh /usr/local/bin/saml-forward.sh
ENTRYPOINT ["/entrypoint.sh"]
