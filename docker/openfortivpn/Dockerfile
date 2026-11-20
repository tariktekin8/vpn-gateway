# Build context: docker/
# openfortivpn built from source: distro packages lag behind on --saml-login fixes.
FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc AS build
ARG OPENFORTIVPN_VERSION=v1.24.1
# The tag can be moved upstream; the build stops unless it is still this commit.
ARG OPENFORTIVPN_COMMIT=a40a2d588733d48534eb78cd17b90142e5ba039b
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git build-essential autoconf automake pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "$OPENFORTIVPN_VERSION" https://github.com/adrienverge/openfortivpn.git /src \
    && cd /src && test "$(git rev-parse HEAD)" = "$OPENFORTIVPN_COMMIT" \
    && ./autogen.sh && ./configure --prefix=/usr/local --sysconfdir=/etc && make -j"$(nproc)" && make install

FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates ppp socat iproute2 iptables iputils-ping curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/bin/openfortivpn /usr/local/bin/openfortivpn
COPY common/hub-link.sh common/hide-cookies.sh /usr/local/bin/
COPY openfortivpn/entrypoint.sh /entrypoint.sh
# A Windows checkout has no executable bit.
RUN chmod 755 /entrypoint.sh /usr/local/bin/hub-link.sh /usr/local/bin/hide-cookies.sh
ENTRYPOINT ["/entrypoint.sh"]
