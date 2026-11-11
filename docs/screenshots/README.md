# SwitchDeck screenshots

All screenshots use the built-in demo mode (`SD_DEMO=1`): four simulated switches, one of each supported model.

## Dashboard
Fleet health at a glance: switches online, active ports, PoE draw against budget, throughput, firmware compliance and recent events.

![Dashboard](dashboard.png)

Light theme:

![Dashboard, light theme](dashboard-light.png)

## Devices
Every switch with status, model, firmware, a live port strip, PoE, CPU/temperature, uptime and last backup.

![Devices](devices.png)

## Device detail
Front-panel port map (⚡ PoE delivering, SFP and uplink tags, disabled ports hatched). Clicking a port shows its traffic, PoE, connected device and actions: power-cycle PoE, enable/disable, description, cable test and uplink/access role.

![Device overview](device-overview.png)

Per-port table:

![Device ports](device-ports.png)

## Configuration backups
Normalized JSON snapshots with a diff between versions; restore by section.

![Backup diff](backup-diff.png)

## Topology
Switches and their connected devices, from each switch's neighbor table.

![Topology](topology.png)

## Firmware
Version compliance by model, an image library, and one-at-a-time upgrades.

![Firmware](firmware.png)

## Alerts & events
Link changes, configuration drift, syslog from the switches and user actions. A separate tab holds the audit log.

![Events](events.png)

## Settings
Users and roles, TACACS+ sign-in, shared switch credential profiles, management baseline, sites, and read-only API tokens for dashboards.

![Settings](settings.png)

## Sign-in and mobile

| Sign-in | Phone width |
|---|---|
| ![Sign-in](login.png) | ![Mobile](mobile.png) |
