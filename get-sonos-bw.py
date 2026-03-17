"""
Pull the data in based on some ifconfig parsing

Output the data to InfluxDB 2 using the Point API.
"""
import toml
import requests
import time
import sys
from bs4 import BeautifulSoup
import lxml
from ifconfigparser import IfconfigParser
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)



def parse_html(response, sample_dict):
    """
    Given a response from requests, look for the metrics table and parse into dict.

    From the metrics table, create a dictionary that has only sample fields
    Calls create_samples
    """
    # Parsing the HTML / XML
    # Could I use a smaller library to get the clean text?
    soup = BeautifulSoup(response.content, "xml")
    # print("The Soup Text Follows")
    soup_text = soup.get_text()
    # print(soup_text)

    interfaces = IfconfigParser(console_output=soup_text)

    br0 = interfaces.get_interface(name="br0")

    br0_full = br0._asdict()

    # print(f"br0 interface details {br0_full}")

    # I only want certain fields. Expand list to get more fields.

    fields = [
        "rx_packets",
        "rx_errors",
        "rx_dropped",
        "rx_bytes",
        "tx_packets",
        "tx_errors",
        "tx_dropped",
        "tx_bytes",
        "tx_collisions"
    ]

    # dictionary comprehension saves the day
    # Put the keys and values in the list "fields" into new dict
    sample_dict = {k: br0_full[k] for k in fields}

    # print(f"New sample dictionary is {sample_dict}")

    # Not 100% sure why I need to do this. /shrug
    return sample_dict


def scrape_speaker(speaker, influx_config, influx_bucket, influx_measurement, retries):
    """Fetch metrics from one speaker and write to InfluxDB. Returns True on success."""
    speaker_ip = speaker["ip"]
    speaker_host = speaker["host"]
    speaker_region = speaker["region"]

    speaker_bw_url = "http://" + speaker_ip + ":1400/status/ifconfig"

    start = time.time()
    try:
        response = requests.get(speaker_bw_url, timeout=10)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        print(
            f"WARNING: could not reach {speaker_host} ({speaker_ip}): {e}\n"
            f"  The speaker's IP may have changed — re-run discover-sonos.py to refresh config.toml",
            file=sys.stderr,
        )
        return False
    except requests.exceptions.RequestException as e:
        print(f"WARNING: request failed for {speaker_host} ({speaker_ip}): {e}", file=sys.stderr)
        return False

    if not response.ok:
        print(f"WARNING: non-OK response from {speaker_host} ({speaker_ip}): {response.status_code}", file=sys.stderr)
        return False

    total_time = int((time.time() - start) * 1000)
    elapsed_time = int(response.elapsed.total_seconds() * 1000)

    try:
        sample_dict = {}
        sample_dict = parse_html(response, sample_dict)
    except Exception as e:
        print(f"WARNING: failed to parse response from {speaker_host} ({speaker_ip}): {e}", file=sys.stderr)
        return False

    sample_dict["total_time"] = total_time
    sample_dict["elapsed_time"] = elapsed_time

    point = Point(influx_measurement).tag("host", speaker_host).tag("region", speaker_region)
    for key, value in sample_dict.items():
        # Cast to float to match the existing InfluxDB schema. The original line protocol
        # implementation wrote integers without the 'i' suffix, which InfluxDB stored as
        # floats. The Point API would write Python ints as integers (with 'i' suffix),
        # causing a type conflict on existing fields.
        point = point.field(key, float(value))

    try:
        with InfluxDBClient(
            url=influx_config["url"],
            token=influx_config["token"],
            org=influx_config["org"],
            verify_ssl=influx_config.get("verify_ssl", True),
            retries=retries,
        ) as client:
            with client.write_api(write_options=SYNCHRONOUS) as writer:
                writer.write(bucket=influx_bucket, record=point)
    except Exception as e:
        print(f"WARNING: InfluxDB write failed for {speaker_host}: {e}", file=sys.stderr)
        return False

    return True


def main():
    # Read in settings from TOML file
    # Set .gitignore for config.toml. See config-example.toml

    config = toml.load("config.toml")
    # print(config)

    speakers = config["speakers"]

    influx_bucket = config["influx2"]["bucket"]
    influx_measurement = config["influx2"]["measurement"]

    retries = urllib3.Retry(connect=3, read=2, redirect=3)

    influx_config = config["influx2"]
    results = [scrape_speaker(s, influx_config, influx_bucket, influx_measurement, retries) for s in speakers]

    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
