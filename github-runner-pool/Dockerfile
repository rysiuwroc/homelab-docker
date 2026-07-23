FROM ubuntu:24.04

ARG RUNNER_VERSION
ARG RUNNER_SHA256

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    RUNNER_ALLOW_RUNASROOT=0

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The runner image carries only general CI execution, source, archive, Python,
# Docker, and network tooling. Compilers and language-specific SDKs belong in
# the workflows that require them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        unzip \
        xz-utils \
        zstd \
        tar \
        gzip \
        bzip2 \
        zip \
        python3 \
        python3-pip \
        iproute2 \
        iputils-ping \
        dnsutils \
        netcat-openbsd \
        procps \
        util-linux \
        libicu74 \
        libkrb5-3 \
        zlib1g \
        libssl3 \
        libcurl4 \
        liblttng-ust1 \
        libunwind8 \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL --retry 3 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

RUN test -n "${RUNNER_VERSION}" \
    && [[ "${RUNNER_VERSION}" =~ ^[0-9]+(\.[0-9]+)+$ ]] \
    && test -n "${RUNNER_SHA256}" \
    && [[ "${RUNNER_SHA256}" =~ ^[[:xdigit:]]{64}$ ]] \
    && install -d -m 0755 /opt/actions-runner-dist \
    && curl -fsSL --retry 3 \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
        -o /tmp/actions-runner.tar.gz \
    && printf '%s  %s\n' "${RUNNER_SHA256}" /tmp/actions-runner.tar.gz | sha256sum -c - \
    && tar -xzf /tmp/actions-runner.tar.gz -C /opt/actions-runner-dist --no-same-owner \
    && rm -f /tmp/actions-runner.tar.gz

RUN groupadd --gid 1001 runner \
    && useradd --uid 1001 --gid runner --create-home --home-dir /home/runner --shell /bin/bash runner \
    && install -d -o runner -g runner -m 0755 /runner /runner/config /runner/_work /runner/_tool \
    && chown -R runner:runner /opt/actions-runner-dist

COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh

WORKDIR /runner
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
