# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Two-script Python tool:
- `discover-sonos.py` — scans the local network for Sonos speakers via SSDP, fetches room names and bonding roles via UPnP SOAP, and generates `config.toml`
- `get-sonos-bw.py` — reads `config.toml`, scrapes network interface metrics from each speaker's built-in HTTP status page, and writes them to InfluxDB 2 via the `Point` API; intended to run via cron every 5 minutes

## Setup

```bash
# Create and activate virtualenv
python3 -m venv .env
source .env/bin/activate

# Install dependencies
pip install -r requirements.txt

# Discover speakers and generate config.toml
python3 discover-sonos.py --output config.toml
# Then fill in the [influx2] section of config.toml with InfluxDB credentials

# Or configure manually
cp example-config.toml config.toml
# Edit config.toml with your Sonos IPs and InfluxDB credentials
```

## Running

```bash
# Must be run from the repo root (config.toml is loaded with a relative path)
cd /path/to/sonos-scraper
python3 get-sonos-bw.py
```

## Cron deployment

```crontab
*/5 * * * * cd /your/dir/sonos-scraper && python3 get-sonos-bw.py >> /tmp/sonos-scraper.log
```

## Architecture

### `discover-sonos.py`

1. SSDP multicast M-SEARCH to `239.255.255.250:1900` with `ST: urn:schemas-upnp-org:device:ZonePlayer:1`; sends 3 packets 300ms apart for UDP reliability, then collects source IPs from unicast responses until timeout
2. Optionally TCP-scans a CIDR subnet on port 1400 via `ThreadPoolExecutor` (`--subnet`); rejects subnets larger than /20 (4096 addresses)
3. Fetches full zone group state via `GetZoneGroupState` SOAP call to `ZoneGroupTopology/Control` from one speaker — returns all members (including bonded satellites nested as `<Satellite>` elements) with their zone names and `HTSatChanMapSet` channel roles
4. Maps `HTSatChanMapSet` channel codes to host-tag role suffixes: `SW`→`sub`, `LR`→`left-surround`, `RR`→`right-surround`; `LF,RF` together = coordinator (no suffix)
5. Falls back to per-speaker `GetZoneAttributes` SOAP calls if topology fetch fails
6. Renders `[[speakers]]` array-of-tables + `[influx2]` placeholder; preserves existing `[influx2]` if output file already exists

### `get-sonos-bw.py`

All scraping logic is in `get-sonos-bw.py`:

1. Loads `config.toml` (relative path — must run from repo root)
2. Iterates over all `[[speakers]]` entries; for each one:
   - Fetches `http://<speaker_ip>:1400/status/ifconfig` (unauthenticated Sonos endpoint)
   - Parses the HTML/XML response with BeautifulSoup + lxml, then extracts the `br0` interface via `ifconfigparser`
   - Builds an `influxdb_client.Point` with tags and fields, then writes to InfluxDB 2 via `influxdb_client`, constructing `InfluxDBClient` directly from the parsed `[influx2]` config values
3. Exits with code 1 if any speaker failed; other speakers still run regardless.

Both `[influx2]` and `[[speakers]]` sections are parsed by `toml.load()` and consumed manually. `InfluxDBClient.from_config_file()` is not used (it uses `configparser` which cannot handle TOML array-of-tables syntax).

SSL verification is disabled for InfluxDB (`verify_ssl = false` in config) and urllib3 warnings are suppressed globally.

Per-speaker errors (non-OK HTTP response, request timeout, parse failure, InfluxDB write failure) print a warning to stderr and return False; they do not block other speakers.

The `Point` API handles escaping of tag values and measurement names internally. All field values are cast to `float` to match the existing InfluxDB schema — the original line protocol implementation wrote integers without the `i` suffix, which InfluxDB stored as floats; writing native Python ints via `Point` would cause a type conflict.

## Config

`config.toml` is gitignored. Use `example-config.toml` as the template. Key fields:

- `[[speakers]]` — one block per speaker: `ip`, `host` (InfluxDB host tag), `region` (InfluxDB region tag)
- `[influx2]` — `url`, `org`, `token`, `bucket`, `measurement`, `verify_ssl`
