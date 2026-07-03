# Kindle Dashboard

Transform an old Kindle Paperwhite into a remotely managed e-ink smart display.

## Features

### Dashboard Themes

- Home Dashboard
- Minimal Weather
- Server Monitor
- Travel Weather
- Upcoming: Maarif Calendar

### Remote Management

Manage the Kindle from any browser:

- Theme selection
- Weather location settings
- Display toggles
- Dashboard refresh
- Push updates
- Autostart controls
- Front light controls
- Kindle restart

### System Monitoring

- Weather forecast
- Server statistics
- Pi-hole statistics
- Tailscale status
- Network information

### Mobile Friendly

Responsive web interface accessible from:

- Desktop
- Tablet
- Mobile
- Tailscale remote connection

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

## Roadmap

### Planned

- Maarif Calendar Theme
- Theme Playlist Engine
- Photo Gallery Mode
- Health Dashboard
- Shopping List Dashboard
- Additional Plugins

## Hardware

Tested on:

- Kindle Paperwhite 1 (PW1)
- Ubuntu Linux

## License

MIT
