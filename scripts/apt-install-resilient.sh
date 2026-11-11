#!/usr/bin/env bash
# Retry apt package installation against independent Ubuntu mirrors.
# Usage: bash scripts/apt-install-resilient.sh package [package ...]
set -Eeuo pipefail

if (($# == 0)); then
  echo "Uso: apt-install-resilient.sh pacote [pacote ...]" >&2
  exit 2
fi

PACKAGES=("$@")
MISSING=()
for package in "${PACKAGES[@]}"; do
  if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -qx 'install ok installed'; then
    MISSING+=("$package")
  fi
done

if (("${#MISSING[@]}" == 0)); then
  echo "Pacotes já instalados: ${PACKAGES[*]}"
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
BACKUP_DIR="$(mktemp -d)"
mapfile -t SOURCE_FILES < <(find /etc/apt -maxdepth 2 -type f \( -name '*.list' -o -name '*.sources' \) -print 2>/dev/null | sort)
declare -A SOURCE_BACKUPS=()
for source_file in "${SOURCE_FILES[@]}"; do
  backup="$BACKUP_DIR/$(printf '%s' "$source_file" | sed 's#/#_#g')"
  cp -f "$source_file" "$backup"
  SOURCE_BACKUPS["$source_file"]="$backup"
done

restore_sources() {
  for source_file in "${SOURCE_FILES[@]}"; do
    if [[ -n "${SOURCE_BACKUPS[$source_file]:-}" && -f "${SOURCE_BACKUPS[$source_file]}" ]]; then
      sudo cp -f "${SOURCE_BACKUPS[$source_file]}" "$source_file"
    fi
  done
}

cleanup() {
  restore_sources
  rm -rf "$BACKUP_DIR"
}
trap cleanup EXIT

try_install() {
  sudo apt-get update \
    -o Acquire::Retries=3 \
    -o Acquire::http::Timeout=25 \
    -o Acquire::https::Timeout=25 \
    -o Acquire::ForceIPv4=true \
    -o Acquire::Queue-Mode=access
  sudo apt-get install -y --no-install-recommends \
    -o Acquire::Retries=3 \
    -o Acquire::http::Timeout=25 \
    -o Acquire::https::Timeout=25 \
    -o Acquire::ForceIPv4=true \
    "${MISSING[@]}"
}

echo "Instalando pacotes: ${MISSING[*]} (tentativa no espelho configurado)"
if try_install; then
  echo "Pacotes instalados pelo espelho Ubuntu padrão."
  exit 0
fi

for mirror in "https://mirror.math.princeton.edu/pub/ubuntu" "https://mirrors.edge.kernel.org/ubuntu"; do
  restore_sources
  echo "Espelho padrão indisponível; alternando para $mirror"
  for source_file in "${SOURCE_FILES[@]}"; do
    sudo sed -E -i "s#https?://[^[:space:]\"']+/ubuntu/?#$mirror#g" "$source_file"
  done
  if try_install; then
    echo "Pacotes instalados pelo espelho alternativo $mirror."
    exit 0
  fi
done

echo "ERRO: apt falhou no espelho padrão e nos espelhos alternativos." >&2
exit 20
