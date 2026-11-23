# syntax=docker/dockerfile:1.7
# Runner-binary refresh of local/arc-ci-runner: GitHub stops serving deprecated
# runner versions (v2.336.0 exits with "deprecated and cannot receive messages").
# Only bin/ and externals/ are replaced; everything else in the base is kept.
FROM local/arc-ci-runner:2026-07-26
ARG RUNNER_VERSION=2.338.2
USER runner
ADD --chown=runner:runner --checksum=sha256:baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40 \
    https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz /tmp/actions-runner.tar.gz
RUN rm -rf /home/runner/bin /home/runner/externals \
 && tar -xzf /tmp/actions-runner.tar.gz -C /home/runner \
 && rm -f /tmp/actions-runner.tar.gz
