# Installing SwitchDeck from GitHub

## Install on a Docker host / VM
```bash
curl -fsSL https://raw.githubusercontent.com/schmittty/switchdeck/main/install.sh | sudo bash

# lab dry run with 4 simulated switches
curl -fsSL https://raw.githubusercontent.com/schmittty/switchdeck/main/install.sh | sudo bash -s -- --demo
```
The installer:
- writes `/opt/switchdeck/docker-compose.yml` and `.env`, auto-filling `SD_PUBLIC_HOST` with the host's IP;
- pulls `ghcr.io/schmittty/switchdeck:latest`, or builds from this repo on the host if that image isn't published or reachable (needs `git`);
- starts the container and prints the URL and the first sign-in (`admin` / `admin`, changed at first login).

Next, set up users, roles and optional TACACS+ sign-in: see [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).

## Day to day
```bash
sudo /opt/switchdeck/install.sh update             # pull (or rebuild) the newest version and restart
sudo /opt/switchdeck/install.sh status
sudo nano /opt/switchdeck/.env && cd /opt/switchdeck && sudo docker compose up -d   # change settings
sudo /opt/switchdeck/install.sh uninstall          # stop; data kept
sudo /opt/switchdeck/install.sh uninstall --purge  # stop and delete DB, backups, firmware
```

## Prebuilt images (optional, faster updates)
`.github/workflows/docker-publish.yml` builds a multi-arch image (amd64/arm64) on every push to `main` and publishes it to `ghcr.io/schmittty/switchdeck` (tags `latest` and `sha-<commit>`). After the first run:
- GitHub → your profile → **Packages → switchdeck → Package settings → Change visibility → Public**. New packages start private even when the repo is public.
- To pin a build, set `SD_IMAGE=ghcr.io/schmittty/switchdeck:sha-<7 chars>` in `/opt/switchdeck/.env`.

Without the workflow, everything still works: the installer builds locally, and `update` rebuilds from the latest commit.

## Other ways to run
- From a clone: `cp .env.example .env && docker compose -f docker-compose.build.yml up -d --build`
- Without cloning: `SD_BUILD_CONTEXT=https://github.com/schmittty/switchdeck.git#main docker compose -f docker-compose.build.yml up -d --build` (with a `.env` beside the compose file)

## Back up the data
Everything lives in the `switchdeck_switchdeck-data` volume: the database, config backups, firmware library and the credential key.
```bash
docker run --rm -v switchdeck_switchdeck-data:/d -v "$PWD":/o alpine tar czf /o/switchdeck-data.tgz -C /d .
```
