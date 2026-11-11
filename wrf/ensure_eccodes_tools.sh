#!/usr/bin/env bash
# Sideral resilient ecCodes bootstrap.
# Source this file before using grib_set/grib_copy/grib_count.
set -Eeuo pipefail

sideral_ensure_eccodes_tools() {
  local tool mirror source_file backup_dir wrapper_dir image
  local -a tools=(grib_set grib_copy grib_count)
  local -a sources=()
  local -A backups=()

  _sideral_tools_ready() {
    local t
    for t in "${tools[@]}"; do command -v "$t" >/dev/null 2>&1 || return 1; done
    return 0
  }

  if _sideral_tools_ready; then
    echo "ecCodes CLI já disponível no runner."
    return 0
  fi

  export DEBIAN_FRONTEND=noninteractive
  backup_dir="$(mktemp -d)"
  mapfile -t sources < <(find /etc/apt -maxdepth 2 -type f \( -name '*.list' -o -name '*.sources' \) -print 2>/dev/null | sort)
  for source_file in "${sources[@]}"; do
    cp -f "$source_file" "$backup_dir/$(printf '%s' "$source_file" | sed 's#/#_#g')"
    backups["$source_file"]="$backup_dir/$(printf '%s' "$source_file" | sed 's#/#_#g')"
  done

  _sideral_restore_sources() {
    for source_file in "${sources[@]}"; do
      [[ -n "${backups[$source_file]:-}" && -f "${backups[$source_file]}" ]] && sudo cp -f "${backups[$source_file]}" "$source_file"
    done
  }

  _sideral_try_apt() {
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
      libeccodes-tools
  }

  echo "ecCodes ausente; tentando repositórios Ubuntu com retry e timeout controlados."
  if _sideral_try_apt; then
    _sideral_restore_sources
    rm -rf "$backup_dir"
    _sideral_tools_ready || { echo "Pacotes instalados, mas ecCodes CLI incompleto." >&2; return 11; }
    return 0
  fi

  # Canonical/Azure mirror DNS outages have affected GitHub-hosted Ubuntu runners.
  # Rotate to independent Ubuntu mirrors without changing package suites or signing keys.
  for mirror in "https://mirror.math.princeton.edu/pub/ubuntu" "https://mirrors.edge.kernel.org/ubuntu"; do
    _sideral_restore_sources
    echo "apt primary mirror indisponível; tentando espelho alternativo: $mirror"
    for source_file in "${sources[@]}"; do
      sudo sed -E -i "s#https?://[^[:space:]\"']+/ubuntu/?#$mirror#g" "$source_file"
    done
    if _sideral_try_apt; then
      _sideral_restore_sources
      rm -rf "$backup_dir"
      _sideral_tools_ready || { echo "Pacotes instalados, mas ecCodes CLI incompleto." >&2; return 12; }
      return 0
    fi
  done

  _sideral_restore_sources
  rm -rf "$backup_dir"
  image="${SIDERAL_WRF_IMAGE:-dtcenter/wps_wrf:latest}"
  if command -v docker >/dev/null 2>&1; then
    echo "apt indisponível; verificando ecCodes dentro da imagem WRF: $image"
    if docker pull "$image" && docker run --rm --entrypoint sh "$image" -c 'command -v grib_set && command -v grib_copy && command -v grib_count' >/dev/null 2>&1; then
      wrapper_dir="${RUNNER_TEMP:-/tmp}/sideral-eccodes-bin"
      mkdir -p "$wrapper_dir"
      for tool in "${tools[@]}"; do
        cat > "$wrapper_dir/$tool" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
exec docker run --rm --entrypoint "$tool" -v "${PWD}:${PWD}" -w "${PWD}" "$image" "\$@"
EOF
        chmod +x "$wrapper_dir/$tool"
      done
      export PATH="$wrapper_dir:$PATH"
      [[ -n "${GITHUB_PATH:-}" ]] && printf '%s\n' "$wrapper_dir" >> "$GITHUB_PATH"
      echo "ecCodes CLI será executado pelo container WRF; paths do workspace permanecem montados."
      return 0
    fi
  fi

  echo "ERRO: não foi possível obter ecCodes via apt (incluindo espelhos alternativos) nem via container WRF." >&2
  return 20
}

sideral_ensure_eccodes_tools
