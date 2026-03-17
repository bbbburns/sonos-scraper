# Sonos Speakers Bandwidth Scraper

## Purpose

Collect the bandwidth metrics for one or more Sonos speakers.

Publish these metrics to an InfluxDB V2 time series database.

Graph these metrics with Grafana to see how much bandwidth you're using on
average.

![Grafana Screenshot](att-scraper-grafana-screenshot.png)

## Usage

1. Clone this repo into some directory on some server
2. Install the python requirements from requirements.txt
3. Run `discover-sonos.py` to auto-generate config.toml (see below), or copy example-config.toml and fill it in manually
4. Fill in the `[influx2]` section of config.toml with your InfluxDB credentials
5. Configure cron to call get-sonos-bw.py every 5 minutes

## Discovering Speaker IPs and Names

`discover-sonos.py` scans your local network for Sonos speakers and generates a ready-to-use config.toml with correct IPs and names.

**SSDP discovery (recommended)** — no arguments needed, works on any network:

```bash
python3 discover-sonos.py
```

**With TCP fallback** — useful if SSDP multicast is blocked on your network (must be /20 or smaller):

```bash
python3 discover-sonos.py --subnet 192.168.1.0/24
```

**Write directly to config.toml:**

```bash
python3 discover-sonos.py --output config.toml
```

If `config.toml` already exists, the existing `[influx2]` credentials are preserved and only the `[[speakers]]` blocks are regenerated.

**All options:**

```
--subnet SUBNET    CIDR range for TCP port scan fallback (e.g. 192.168.1.0/24)
--region REGION    Region tag for all discovered speakers (default: us-east)
--output OUTPUT    Write config to this file (prints to stdout if omitted)
--timeout TIMEOUT  SSDP discovery timeout in seconds (default: 3)
```

The script uses SSDP multicast to find all Sonos speakers, then fetches each speaker's user-assigned room name via UPnP. Bonded speakers (soundbar + sub + surround pair) are detected automatically from the zone topology and named with role suffixes:

| Role | Example host tag |
|---|---|
| Soundbar (coordinator) | `living-room-sonos` |
| Sub | `living-room-sub-sonos` |
| Left surround | `living-room-left-surround-sonos` |
| Right surround | `living-room-right-surround-sonos` |

## Recommended Cron Configuration

Cron needs absolute paths for things and I've referred to the config file with
a relative path. You can fix this with a `cd`. The following executes every 5
minutes, uses the virtualenv's Python directly (so dependencies are available),
and captures both stdout and stderr to a log file.

```crontab
*/5 * * * * cd /your/dir/sonos-scraper && .env/bin/python3 get-sonos-bw.py >> /tmp/sonos-scraper.log 2>&1
```

## Metrics Collected

From each speaker's `http://<speaker_ip>:1400/status/ifconfig`:
1. TX Packet Count
2. TX Byte Count
3. TX Packet Error Count
4. TX Packet Drop Count
5. TX Collisions

6. RX Packet Count
7. RX Byte Count
8. RX Packet Error Count
9. RX Packet Drop Count

10. Time taken to complete request to each Sonos speaker (total_time)
11. Python response.elapsed time (elapsed_time)

## Implementation

- `discover-sonos.py` — finds speakers via SSDP multicast; fetches room names and bonding roles via UPnP SOAP; generates config.toml
- `get-sonos-bw.py` — reads config.toml, scrapes each speaker, writes metrics to InfluxDB
- Automate script run with cron
- Import configuration with TOML (because it's what's cool with kids these days)
- Scrape Sonos speaker HTML pages (unauthenticated) with requests
- Parse HTML and XML with BeautifulSoup (beautifulsoup4)
- Parse ifconfig with ifconfigparser
- Send data to InfluxDB 2 via the `Point` API (handles escaping and serialization internally)
- Graph results with Grafana

## Configurable Parameters

Each speaker is configured as a `[[speakers]]` block in config.toml:

```toml
[[speakers]]
ip = "192.168.1.X"       # Sonos speaker IP
host = "name-sonos-1"    # InfluxDB host tag
region = "us-east"       # InfluxDB region tag
```

Add one block per speaker. InfluxDB connection parameters:

```toml
[influx2]
url = "https://192.168.1.X:8086"  # InfluxDB URL (IP and port)
org = "your-org"                   # InfluxDB organization
token = "your-token"               # InfluxDB auth token
verify_ssl = false                 # Set true if using a valid cert
bucket = "your-bucket"             # Destination bucket
measurement = "net"                # Measurement name (suggest "net")
```

## Limitations
- Tested with Influx2 client and InfluxDB 2.7
- Only tested on Linux systems
- Per-speaker errors print a warning to stderr; script exits 1 if any speaker fails
- Speaker IPs are stored statically in config.toml — if a speaker's IP changes (DHCP reassignment), scraping will silently produce gaps in Grafana. Configure DHCP reservations for your speakers, or re-run `discover-sonos.py --output config.toml` to refresh. The scraper will print a specific warning suggesting this when a speaker is unreachable.

## Example Grafana Queries

RX Bits Per Second
```
SELECT non_negative_derivative(mean("rx_bytes"), 1s) *8 FROM "net" WHERE ("host"::tag = 'sonos-name') AND $timeFilter GROUP BY time($__interval) fill(null)
```

TX Bits Per Second
```
SELECT non_negative_derivative(mean("tx_bytes"), 1s) *8 FROM "net" WHERE ("host"::tag = 'sonos-name') AND $timeFilter GROUP BY time($__interval) fill(null)
```

Will these queries handle counter wrapping? I don't know. They have
"non-negative derivative" and that's my attempt to deal with that. I don't know
what the math does when the counter wraps around.

I've found it's helpful to also graph the packets per second as an indicator
that you might have had a byte counter wrap. It's trivial in grafana to modify
the above to tx and rx_pkts and stop multiplying by 8, since you don't need to
convert from bytes to bits when you're counting packets.
