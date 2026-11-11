#!/usr/bin/env bash
# Retry apt package installation using the runner mirror list and independent Ubuntu mirrors.
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
APT_MIRROR_LIST="/etc/apt/apt-mirrors.txt"
APT_MIRROR_BACKUP="$BACKUP_DIR/apt-mirrors.txt"
HAD_APT_MIRROR_LIST=0
mapfile -t SOURCE_FILES < <(find /etc/apt -maxdepth 2 -type f \( -name '*.list' -o -name '*.sources' \) -print 2>/dev/null | sort)
declare -A SOURCE_BACKUPS=()
for source_file in "${SOURCE_FILES[@]}"; do
  backup="$BACKUP_DIR/$(printf '%s' "$source_file" | sed 's#/#_#g')"
  sudo cp -f "$source_file" "$backup"
  SOURCE_BACKUPS["$source_file"]="$backup"
done
if sudo test -f "$APT_MIRROR_LIST"; then
  sudo cp -f "$APT_MIRROR_LIST" "$APT_MIRROR_BACKUP"
  HAD_APT_MIRROR_LIST=1
fi

restore_sources() {
  for source_file in "${SOURCE_FILES[@]}"; do
    if [[ -n "${SOURCE_BACKUPS[$source_file]:-}" && -f "${SOURCE_BACKUPS[$source_file]}" ]]; then
      sudo cp -f "${SOURCE_BACKUPS[$source_file]}" "$source_file"
    fi
  done
  if (( HAD_APT_MIRROR_LIST == 1 )); then
    sudo cp -f "$APT_MIRROR_BACKUP" "$APT_MIRROR_LIST"
  else
    sudo rm -f "$APT_MIRROR_LIST"
  fi
}

cleanup() {
  restore_sources
  rm -rf "$BACKUP_DIR"
}
trap cleanup EXIT

set_mirrorlist() {
  local primary="${1:?primary mirror required}"
  local secondary="${2:?secondary mirror required}"
  # Ubuntu GitHub runners use mirror+file:/etc/apt/apt-mirrors.txt.
  printf '%s\tpriority:1\n%s\tpriority:2\n' "${primary%/}/" "${secondary%/}/" |
    sudo tee "$APT_MIRROR_LIST" >/dev/null
  # Support older images with direct archive URLs too.
  for source_file in "${SOURCE_FILES[@]}"; do
    sudo sed -E -i "s#https?://[^[:space:]\"']+/ubuntu/?#${primary%/}/#g" "$source_file"
  done
}

try_install() {
  sudo timeout --signal=TERM --kill-after=10s 180s apt-get update \
    -o Acquire::Retries=1 \
    -o Acquire::http::Timeout=20 \
    -o Acquire::https::Timeout=20 \
    -o Acquire::ForceIPv4=true \
    -o Acquire::Queue-Mode=access
  sudo timeout --signal=TERM --kill-after=10s 480s apt-get install -y --no-install-recommends \
    -o Acquire::Retries=1 \
    -o Acquire::http::Timeout=20 \
    -o Acquire::https::Timeout=20 \
    -o Acquire::ForceIPv4=true \
    "${MISSING[@]}"
}

echo "Instalando pacotes: ${MISSING[*]} (espelho Ubuntu padrão, timeout limitado)"
if try_install; then
  echo "Pacotes instalados pelo espelho Ubuntu padrão."
  exit 0
fi

for mirror in "https://mirror.math.princeton.edu/pub/ubuntu" "https://mirrors.edge.kernel.org/ubuntu"; do
  restore_sources
  echo "Espelho padrão indisponível; prioridade apt passa para $mirror"
  if [[ "$mirror" == "https://mirror.math.princeton.edu/pub/ubuntu" ]]; then
    set_mirrorlist "$mirror" "https://mirrors.edge.kernel.org/ubuntu"
  else
    set_mirrorlist "$mirror" "https://mirror.math.princeton.edu/pub/ubuntu"
  fi
  if try_install; then
    echo "Pacotes instalados pelo espelho alternativo $mirror."
    exit 0
  fi
done

echo "ERRO: apt falhou no espelho padrão e nos espelhos alternativos." >&2
exit 20
