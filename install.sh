#!/usr/bin/env bash
# SwitchDeck installer / updater for a Docker host or VM.
#
#   curl -fsSL https://raw.githubusercontent.com/schmittty/switchdeck/main/install.sh | sudo bash
#   sudo bash install.sh                 # install using the published GHCR image (builds from the repo if none is published yet)
#   sudo bash install.sh update          # pull the latest image and restart
#   sudo bash install.sh --build         # build from the GitHub repo on this host instead of pulling
#   sudo bash install.sh --demo          # install with 4 simulated switches for a lab dry run
#   sudo bash install.sh uninstall       # stop and remove the container (data volume kept)
#   sudo bash install.sh uninstall --purge   # ...and delete all data (backups, DB, firmware)
#
# Private repo / private package: export GITHUB_USER=<you> GITHUB_TOKEN=<PAT with read:packages,repo>
set -euo pipefail

REPO="${SD_REPO:-schmittty/switchdeck}"
BRANCH="${SD_BRANCH:-main}"
IMAGE="${SD_IMAGE:-ghcr.io/${REPO,,}:latest}"
DIR="${SD_DIR:-/opt/switchdeck}"

ACTION="install"; BUILD=0; DEMO=0; PURGE=0
for a in "$@"; do
  case "$a" in
    install|update|uninstall|status) ACTION="$a" ;;
    --build) BUILD=1 ;;
    --demo) DEMO=1 ;;
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo bash $0 $*)"
command -v docker >/dev/null || die "Docker is not installed. Install it first: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 plugin is missing (apt install docker-compose-plugin)"
compose() { docker compose --project-directory "$DIR" -f "$DIR/docker-compose.yml" "$@"; }

ghcr_login() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    say "Logging in to ghcr.io as ${GITHUB_USER:-${REPO%%/*}}"
    echo "$GITHUB_TOKEN" | docker login ghcr.io -u "${GITHUB_USER:-${REPO%%/*}}" --password-stdin >/dev/null
  fi
}

write_files() {
  mkdir -p "$DIR"
  cat > "$DIR/docker-compose.yml" <<'YAML'
services:
  switchdeck:
    image: ${SD_IMAGE}
    container_name: switchdeck
    restart: unless-stopped
    network_mode: host
    volumes:
      - switchdeck-data:/data
    env_file: .env
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }

volumes:
  switchdeck-data:
YAML
  if [ ! -f "$DIR/.env" ]; then
    local host_ip
    host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    cat > "$DIR/.env" <<ENV
SD_IMAGE=$IMAGE
SD_HTTP_PORT=8080
SD_FW_PORT=8081
SD_SYSLOG_PORT=5514
SD_PUBLIC_HOST=${host_ip}
SD_POLL_INTERVAL=30
SD_BACKUP_INTERVAL=86400
SD_ADMIN_PASSWORD=
SD_DEMO=$DEMO
ENV
    chmod 600 "$DIR/.env"
    say "Wrote $DIR/.env (switches will reach this host at ${host_ip:-<set SD_PUBLIC_HOST>})"
  else
    sed -i "s|^SD_IMAGE=.*|SD_IMAGE=$IMAGE|" "$DIR/.env"
    [ "$DEMO" -eq 1 ] && sed -i "s|^SD_DEMO=.*|SD_DEMO=1|" "$DIR/.env"
    say "Kept existing $DIR/.env"
  fi
}

build_local() {
  command -v git >/dev/null || die "git is required for --build (apt install git)"
  local url="https://github.com/$REPO.git"
  [ -n "${GITHUB_TOKEN:-}" ] && url="https://${GITHUB_USER:-x-access-token}:${GITHUB_TOKEN}@github.com/$REPO.git"
  if [ -d "$DIR/src/.git" ]; then
    say "Updating source in $DIR/src"
    git -C "$DIR/src" fetch --depth 1 origin "$BRANCH" && git -C "$DIR/src" reset --hard FETCH_HEAD
  else
    say "Cloning $REPO ($BRANCH)"
    rm -rf "$DIR/src"; git clone --depth 1 -b "$BRANCH" "$url" "$DIR/src"
  fi
  git -C "$DIR/src" remote set-url origin "https://github.com/$REPO.git"   # don't leave the token on disk
  local ctx="$DIR/src"; [ -f "$ctx/Dockerfile" ] || ctx="$DIR/src/switchdeck"   # older layout
  [ -f "$ctx/Dockerfile" ] || die "no Dockerfile found in the repo root or ./switchdeck"
  say "Building image switchdeck:local"
  docker build -t switchdeck:local "$ctx"
  IMAGE="switchdeck:local"
}

wait_ready() {
  local port; port="$(grep -E '^SD_HTTP_PORT=' "$DIR/.env" | cut -d= -f2)"; port="${port:-8080}"
  local scheme="http"; grep -qE '^SD_TLS_CERT=.+' "$DIR/.env" && scheme="https"
  say "Waiting for SwitchDeck on :$port"
  for _ in $(seq 1 30); do
    if curl -kfsS "$scheme://127.0.0.1:$port/" >/dev/null 2>&1; then
      local ip; ip="$(grep -E '^SD_PUBLIC_HOST=' "$DIR/.env" | cut -d= -f2)"
      echo
      say "SwitchDeck is up:  $scheme://${ip:-<this-host>}:$port"
      local pw; pw="$(docker exec switchdeck cat /data/initial-admin-password.txt 2>/dev/null || true)"
      if [ -n "$pw" ]; then
        echo "    first sign-in:    $pw   (you'll be asked to set a new password)"
        echo "    users, roles, TACACS+:  https://github.com/$REPO/blob/$BRANCH/docs/AUTHENTICATION.md"
      fi
      echo "    syslog UDP :$(grep -E '^SD_SYSLOG_PORT=' "$DIR/.env" | cut -d= -f2)   firmware HTTP :$(grep -E '^SD_FW_PORT=' "$DIR/.env" | cut -d= -f2)"
      echo "    open these on the host firewall for the switch management VLAN if a firewall is enabled."
      return 0
    fi
    sleep 2
  done
  warn "not answering yet — check: docker logs switchdeck"
}

case "$ACTION" in
  install)
    ghcr_login
    [ "$BUILD" -eq 1 ] && build_local
    write_files
    if [ "$BUILD" -eq 0 ]; then
      say "Pulling $IMAGE"
      if ! compose pull; then
        warn "Could not pull $IMAGE (not published yet, or the package is private). Building from the repo instead."
        build_local
        write_files
      fi
    fi
    compose up -d
    [ -f "$0" ] && [ "$(basename "$0")" = "install.sh" ] && cp -f "$0" "$DIR/install.sh" 2>/dev/null || \
      curl -fsSL ${GITHUB_TOKEN:+-H "Authorization: token $GITHUB_TOKEN"} \
        "https://raw.githubusercontent.com/$REPO/$BRANCH/install.sh" -o "$DIR/install.sh" 2>/dev/null || true
    chmod +x "$DIR/install.sh" 2>/dev/null || true
    wait_ready
    ;;
  update)
    [ -f "$DIR/docker-compose.yml" ] || die "not installed in $DIR"
    ghcr_login
    IMAGE="$(grep -E '^SD_IMAGE=' "$DIR/.env" | cut -d= -f2)"
    if [ "$IMAGE" = "switchdeck:local" ] || [ "$BUILD" -eq 1 ]; then
      build_local; write_files
    elif ! compose pull; then
      warn "Could not pull $IMAGE; rebuilding from the repo"
      build_local; write_files
    fi
    compose up -d
    docker image prune -f >/dev/null
    wait_ready
    ;;
  status)
    compose ps; docker logs --tail 20 switchdeck
    ;;
  uninstall)
    compose down $([ "$PURGE" -eq 1 ] && echo "-v")
    [ "$PURGE" -eq 1 ] && rm -rf "$DIR" && say "Removed $DIR and all SwitchDeck data" || say "Stopped. Data volume kept; reinstall picks it up."
    ;;
esac
