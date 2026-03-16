# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A single-script Python tool that scrapes network interface metrics from a Sonos speaker's built-in HTTP status page and writes them to InfluxDB 2 via the `Point` API. Intended to run via cron every 5 minutes.

## Setup

```bash
# Create and activate virtualenv
python3 -m venv .env
source .env/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp example-config.toml config.toml
# Edit config.toml with your Sonos IP and InfluxDB credentials
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

All logic is in `get-sonos-bw.py`:

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
