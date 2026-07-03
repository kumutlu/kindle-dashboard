# Kindle Dashboard

Transform an old Kindle Paperwhite into a remotely managed e-ink smart display.

<img width="705" height="858" alt="image" src="https://github.com/user-attachments/assets/9b4ffe17-b565-44b9-b9a4-215d12921c65" />


## Overview

Kindle Dashboard is a self-hosted dashboard platform designed for jailbroken Kindle Paperwhite devices.

The project runs on an Ubuntu server, generates optimized e-ink dashboard images, and remotely manages the Kindle through SSH.

The goal is to give old Kindle devices a second life as always-on smart displays.

---

## Features

### Dashboard Themes

Available themes:

- Home Dashboard
- Minimal Weather
- Server Monitor
- Travel Weather

Planned themes:

- Maarif Calendar
- Photo Gallery
- Health Dashboard
- Shopping List

---

### Remote Management

Manage your Kindle from any browser.

Features include:

- Theme selection
- Location configuration
- Display toggles
- Dashboard refresh
- Push to Kindle
- Autostart controls
- Front light controls
- Kindle restart
- System logs

---

### Monitoring

Built-in dashboard widgets include:

- Current weather
- Weather forecast
- Server statistics
- Pi-hole statistics
- Tailscale status
- Network information

---

### Mobile Friendly

The settings interface works on:

- Desktop
- Tablet
- Mobile

Remote access is supported through Tailscale.

---

## Architecture

Ubuntu Server

↓

Dashboard Generator

↓

Flask Settings UI

↓

SSH Connection

↓

Kindle Paperwhite

---

## Hardware

Tested on:

- Kindle Paperwhite 1 (PW1)
- Ubuntu Linux

---

## Roadmap

### Planned Features

- Maarif Calendar Theme
- Theme Playlist Engine
- Photo Gallery Mode
- Additional Dashboard Plugins
- Live Dashboard Preview
- City Search with Geocoding

---

## License

MIT
