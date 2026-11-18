# Build context: docker/
# openfortivpn built from source: distro packages lag behind on --saml-login fixes.
FROM debian:bookworm-slim AS build
ARG OPENFORTIVPN_VERSION=v1.24.1
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git build-essential autoconf automake pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "$OPENFORTIVPN_VERSION" https://github.com/adrienverge/openfortivpn.git /src \
    && cd /src && ./autogen.sh && ./configure --prefix=/usr/local --sysconfdir=/etc && make -j"$(nproc)" && make install

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates ppp socat iproute2 iptables iputils-ping curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/bin/openfortivpn /usr/local/bin/openfortivpn
COPY common/hub-link.sh /usr/local/bin/hub-link.sh
COPY openfortivpn/entrypoint.sh /entrypoint.sh
# A Windows checkout has no executable bit.
RUN chmod 755 /entrypoint.sh /usr/local/bin/hub-link.sh
ENTRYPOINT ["/entrypoint.sh"]
