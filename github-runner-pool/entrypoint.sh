#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

readonly api_base="https://api.github.com/orgs/${GITHUB_ORG:?GITHUB_ORG is required}"
readonly pat_file="/run/secrets/github-runner-api-token"
readonly runner_config_dir="/runner/config"
readonly ready_file="/run/runner-ready"
readonly pid_file="/run/runner.pid"

runner_pid=""
registered="false"
github_pat=""

log() {
    printf '[github-runner] %s\n' "$*" >&2
}

fail() {
    log "ERROR: $*"
    exit 1
}

require_value() {
    local name="$1"
    local value="${!name:-}"
    [[ -n "$value" ]] || fail "$name is required"
}

require_value RUNNER_NAME
require_value RUNNER_GROUP
require_value RUNNER_LABELS
require_value RUNNER_WORKDIR
require_value DOCKER_GID
[[ "$DOCKER_GID" =~ ^[0-9]+$ ]] || fail "DOCKER_GID must be numeric"
(( DOCKER_GID > 0 && DOCKER_GID < 4294967295 )) || fail "DOCKER_GID is out of range"

docker_group="$(getent group "$DOCKER_GID" | cut -d: -f1 || true)"
if [[ -z "$docker_group" ]]; then
    groupadd --system --gid "$DOCKER_GID" docker-host
    docker_group="docker-host"
fi
usermod --append --groups "$docker_group" runner
runuser -u runner -- id -Gn | tr ' ' '\n' | grep -Fxq "$docker_group" \
    || fail "runner did not inherit Docker socket group $docker_group"
[[ -S /var/run/docker.sock ]] || fail "host Docker socket is not mounted"
runuser -u runner -- docker version --format '{{.Server.Version}}' >/dev/null 2>&1 \
    || fail "runner cannot access the host Docker daemon"

[[ -r "$pat_file" ]] || fail "host PAT file is not readable at $pat_file"
[[ -d "$runner_config_dir" ]] || fail "runner config volume is unavailable at $runner_config_dir"

# Read the host-mounted PAT without ever placing it in compose environment or
# printing it. The temporary curl config also keeps it out of process listings.
mapfile -t pat_lines < "$pat_file"
[[ ${#pat_lines[@]} -eq 1 ]] || fail "host PAT file must contain exactly one token line"
github_pat="${pat_lines[0]}"
unset pat_lines
[[ -n "$github_pat" ]] || fail "host PAT file is empty"
[[ "$github_pat" != *[![:graph:]]* ]] || fail "host PAT file must contain one printable token line"

install -d -o runner -g runner -m 0755 "$runner_config_dir" "$RUNNER_WORKDIR"
cp -a /opt/actions-runner-dist/. "$runner_config_dir/"
chown -R runner:runner "$runner_config_dir"
rm -f "$ready_file" "$pid_file"

api_token() {
    local endpoint="$1"
    local curl_config
    local response

    curl_config="$(mktemp)"
    chmod 600 "$curl_config"
    {
        printf 'silent\nshow-error\nfail-with-body\nretry = 3\n'
        printf 'header = "Accept: application/vnd.github+json"\n'
        printf 'header = "X-GitHub-Api-Version: 2022-11-28"\n'
        printf 'header = "Authorization: Bearer %s"\n' "$github_pat"
    } > "$curl_config"

    if ! response="$(curl --config "$curl_config" --request POST "$endpoint")"; then
        rm -f "$curl_config"
        return 1
    fi
    rm -f "$curl_config"

    jq -er '.token // empty' <<< "$response"
}

deregister() {
    local removal_token

    [[ "$registered" == "true" ]] || return 0
    registered="false"
    rm -f "$ready_file"

    if ! removal_token="$(api_token "${api_base}/actions/runners/remove-token")"; then
        log "WARNING: could not obtain a GitHub removal token; runner may need manual removal"
        return 0
    fi

    if ! runuser -u runner -- "$runner_config_dir/config.sh" remove --unattended --token "$removal_token" >/dev/null; then
        log "WARNING: GitHub runner removal failed for ${RUNNER_NAME}"
        return 0
    fi
    unset removal_token
    log "Deregistered ${RUNNER_NAME}"
}

shutdown() {
    local status="$?"
    trap - EXIT TERM INT HUP

    if [[ -n "$runner_pid" ]]; then
        kill -TERM "$runner_pid" 2>/dev/null || true
        wait "$runner_pid" 2>/dev/null || true
    fi
    deregister
    rm -f "$pid_file"
    exit "$status"
}

trap shutdown EXIT TERM INT HUP

registration_token=""
if ! registration_token="$(api_token "${api_base}/actions/runners/registration-token")"; then
    fail "could not obtain a GitHub registration token"
fi

if ! runuser -u runner -- "$runner_config_dir/config.sh" \
    --unattended \
    --url "https://github.com/${GITHUB_ORG}" \
    --token "$registration_token" \
    --name "$RUNNER_NAME" \
    --runnergroup "$RUNNER_GROUP" \
    --labels "$RUNNER_LABELS" \
    --work "$RUNNER_WORKDIR" \
    --disableupdate \
    --replace >/dev/null; then
    fail "GitHub runner registration failed for ${RUNNER_NAME}"
fi
unset registration_token
registered="true"

runuser -u runner -- "$runner_config_dir/run.sh" &
runner_pid="$!"
printf '%s\n' "$runner_pid" > "$pid_file"
touch "$ready_file"
log "Started ${RUNNER_NAME} (org=${GITHUB_ORG}, group=${RUNNER_GROUP})"

wait "$runner_pid"
