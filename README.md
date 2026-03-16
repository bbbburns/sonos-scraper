# Sonos Speaker Bandwidth Scraper

## Purpose

Collect the bandwidth metrics for a Sonos speaker.

Publish these metric to an InfluxDB V2 time series database.

Graph these metrics with Grafana to see how much bandwidth you're using on
average.

![Grafana Screenshot](att-scraper-grafana-screenshot.png)

## Usage

1. Clone this repo into some directory on some server
2. Install the python requirements from requirements.txt
3. Copy example-config.toml to config.toml
4. Configure config.toml with your Sonos speakers (one `[[speakers]]` block each) and InfluxDB values
5. Configure cron to call get-sonos-bw.py every 5 minutes

## Recommended Cron Configuration

Cron needs absolute paths for things and I've referred to the config file with
a relative path. You can fix this with a `cd`. The following executes every 5
minutes, uses the virtualenv's Python directly (so dependencies are available),
and captures both stdout and stderr to a log file.

```crontab
*/5 * * * * cd /your/dir/sonos-scraper && .env/bin/python3 get-sonos-bw.py >> /tmp/sonos-scraper.log 2>&1
```

## Metrics Collected

From: `http://192.168.1.X:1400/status/ifconfig`
1. TX Packet Count
2. TX Byte Count
3. TX Packet Error Count
4. TX Packet Drop Count
5. TX Collisions

6. RX Packet Count
7. RX Byte Count
8. RX Packet Error Count
9. RX Packet Drop Count

9. Time taken to complete request to Sonos speaker (total_time)
10. Python response.elapsed time (elapsed_time)

## Implementation

- Automate script run with cron
- Import configuration with TOML (because it's what's cool with kids these days)
- Scrape Sonos Speaker HTML page (unathenticated) with requests
- Parse HTML and XML with BeautifulSoup (beautifulsoup4)
- Parse ifconfig with ifconfigparser
- Send data to InfluxDB 2 via the `Point` API (handles escaping and serialization internally)
- Graph results with Grafana

## Configurable Parameters

- Speaker IP (one `[[speakers]]` block per speaker in config.toml)
- InfluxDB IP
- InfluxDB Port (default 8086)
- InfluxDB Token
- InfluxDB Bucket
- InfluxDB Org
- InfluxDB Measurement Name (suggest net)
- InfluxDB Host Tag
- InfluxDB Region Tag (name of your house or location)

## Limitations
- Tested with Influx2 client and InfluxDB 2.4.7
- Only tested on Linux systems
- Per-speaker errors print a warning to stderr; script exits 1 if any speaker fails

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
