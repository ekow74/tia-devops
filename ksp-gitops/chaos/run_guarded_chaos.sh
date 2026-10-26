#!/usr/bin/env bash
set -euo pipefail

: "${KSP_ENV:?KSP_ENV is required}"
: "${CHAOS_AUTHORIZED:?CHAOS_AUTHORIZED is required}"

if [[ "${KSP_ENV}" != "staging" ]]; then
  echo "chaos-runner: REFUSE: KSP_ENV must be staging" >&2
  exit 40
fi

if [[ "${CHAOS_AUTHORIZED}" != "true" ]]; then
  echo "chaos-runner: REFUSE: CHAOS_AUTHORIZED must equal true" >&2
  exit 41
fi

if [[ -e /run/ksp-production-sentinel || -e /etc/ksp/production ]]; then
  echo "chaos-runner: REFUSE: production sentinel detected" >&2
  exit 42
fi

case "${1:-}" in
  toxiproxy-up)
    docker compose -f ksp-gitops/chaos/docker-compose.chaos.yml --profile chaos up -d toxiproxy
    ;;
  toxiproxy-down)
    docker compose -f ksp-gitops/chaos/docker-compose.chaos.yml --profile chaos down
    ;;
  *)
    echo "usage: $0 {toxiproxy-up|toxiproxy-down}" >&2
    exit 64
    ;;
esac
