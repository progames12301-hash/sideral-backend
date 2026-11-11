#!/usr/bin/env bash
# Resilient ecCodes bootstrap for GitHub-hosted Ubuntu runners.
# Source this file before using grib_set/grib_copy/grib_count.
set -Eeuo pipefail

sideral_ensure_eccodes_tools() {
  local tool mirror source_file backup_dir wrapper_dir image
  local apt_mirror_list="/etc/apt/apt-mirrors.txt"
  local apt_mirror_backup=""
  local had_apt_mirror_list=0
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
    local backup
    backup="$backup_dir/$(printf '%s' "$source_file" | sed 's#/#_#g')"
    sudo cp -f "$source_file" "$backup"
    backups["$source_file"]="$backup"
  done
  if sudo test -f "$apt_mirror_list"; then
    apt_mirror_backup="$backup_dir/apt-mirrors.txt"
    sudo cp -f "$apt_mirror_list" "$apt_mirror_backup"
    had_apt_mirror_list=1
  fi

  _sideral_restore_sources() {
    for source_file in "${sources[@]}"; do
      if [[ -n "${backups[$source_file]:-}" && -f "${backups[$source_file]}" ]]; then
        sudo cp -f "${backups[$source_file]}" "$source_file"
      fi
    done
    if (( had_apt_mirror_list == 1 )); then
      sudo cp -f "$apt_mirror_backup" "$apt_mirror_list"
    else
      sudo rm -f "$apt_mirror_list"
    fi
  }

  _sideral_set_mirrorlist() {
    local primary="${1:?primary mirror required}"
    local secondary="${2:?secondary mirror required}"
    # GitHub runner images route Ubuntu repositories through
    # mirror+file:/etc/apt/apt-mirrors.txt. Rewrite that actual list, not just
    # the .sources URI, so apt cannot remain pinned to the unhealthy Azure entry.
    printf '%s\tpriority:1\n%s\tpriority:2\n' "${primary%/}/" "${secondary%/}/" |
      sudo tee "$apt_mirror_list" >/dev/null
    # Also support runner images that still use direct Ubuntu archive URLs.
    for source_file in "${sources[@]}"; do
      sudo sed -E -i "s#https?://[^[:space:]\"']+/ubuntu/?#${primary%/}/#g" "$source_file"
    done
  }

  _sideral_try_apt() {
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
      libeccodes-tools
  }

  echo "ecCodes ausente; tentando o espelho Ubuntu padrão com timeout limitado."
  if _sideral_try_apt; then
    _sideral_restore_sources
    rm -rf "$backup_dir"
    _sideral_tools_ready || { echo "Pacotes instalados, mas ecCodes CLI incompleto." >&2; return 11; }
    return 0
  fi

  # Princeton first; Kernel.org is the independent secondary mirror.
  # Invert their priority on the next attempt to avoid depending on either.
  for mirror in "https://mirror.math.princeton.edu/pub/ubuntu" "https://mirrors.edge.kernel.org/ubuntu"; do
    _sideral_restore_sources
    echo "Espelho padrão indisponível; prioridade apt passa para $mirror"
    if [[ "$mirror" == "https://mirror.math.princeton.edu/pub/ubuntu" ]]; then
      _sideral_set_mirrorlist "$mirror" "https://mirrors.edge.kernel.org/ubuntu"
    else
      _sideral_set_mirrorlist "$mirror" "https://mirror.math.princeton.edu/pub/ubuntu"
    fi
    if _sideral_try_apt; then
      _sideral_restore_sources
      rm -rf "$backup_dir"
      _sideral_tools_ready || { echo "Pacotes instalados, mas ecCodes CLI incompleto." >&2; return 12; }
      return 0
    fi
  done

  _sideral_restore_sources
  rm -rf "$backup_dir"

  # Last resort for WRF jobs that already use the official DTC WPS/WRF container.
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
exec docker run --rm --entrypoint "$tool" -v "\${PWD}:\${PWD}" -w "\${PWD}" "$image" "\$@"
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
