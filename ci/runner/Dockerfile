FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 python3-venv sudo xz-utils unzip zip \
    build-essential pkg-config time file lsb-release libicu74 libssl3t64 \
    libkrb5-3 zlib1g liblttng-ust1t64 libunwind8 locales \
    && rm -rf /var/lib/apt/lists/* \
    && locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
# Ubuntu already reserves uid 1000. Use it for the dedicated mounted workspace.
RUN usermod -l ci -d /home/ci -m ubuntu \
    && groupmod -n ci ubuntu \
    && printf 'ci ALL=(ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/ci \
    && chmod 0440 /etc/sudoers.d/ci \
    && mkdir -p /opt/runner /work && chown ci:ci /opt/runner /work
WORKDIR /opt/runner
RUN curl -fL --retry 3 -o /tmp/runner.tar.gz \
      https://github.com/actions/runner/releases/download/v2.338.1/actions-runner-linux-x64-2.338.1.tar.gz \
    && echo '08e9ccb3604d2462c24b5e33604dc30810296ca15cc65f23cffd4317eb144c37  /tmp/runner.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/runner.tar.gz && rm /tmp/runner.tar.gz \
    && chown -R ci:ci /opt/runner
COPY --chmod=755 start.sh /usr/local/bin/start-runner
USER ci
ENTRYPOINT ["/usr/local/bin/start-runner"]
