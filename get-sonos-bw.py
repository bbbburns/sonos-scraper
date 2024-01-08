"""
Pull the data in based on some ifconfig parsing

Output the data to Influx 2 in line protocol

Ideal line protocol for measurement. Line breaks added for clarity.

net,host=name-sonos,region=location tx_bytes=3713275163,tx_pkts=56434892,
                                    tx_err=0,tx_pct=0,rx_bytes=4909425,
                                    rx_pkts109068990,rx_err=0,rx_pct=0,
                                    total_time=1234,elapsed_time=678
"""
import toml
import requests
import time
import sys
from bs4 import BeautifulSoup
import lxml
from ifconfigparser import IfconfigParser
from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def append_fields(line_body, field_dict):
    """
    Given a line_body string that has the measurement and tags, append fields.
#import re
#import ifcfg
    Return the new line_body string.

    Take the list of fields from a dictionary and append them to the end
    of the line_body string. Handle the commas correctly.
    """

    for i, (key, value) in enumerate(field_dict.items()):
        if i + 1 == len(field_dict):
            # last item gets no comma at end
            # also applies if dict has length of 1
            line_body += key + "=" + str(value)
        else:
            line_body += key + "=" + str(value) + ","

    return line_body


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

def main():
    # Read in settings from TOML file
    # Set .gitignore for config.toml. See config-example.toml

    config = toml.load("config.toml")
    # print(config)

    speaker_ip = config["speaker"]["ip"]
    speaker_host = config["speaker"]["host"]
    speaker_region = config["speaker"]["region"]

    influx_bucket = config["influx2"]["bucket"]
    influx_measurement = config["influx2"]["measurement"]

    speaker_bw_url = "http://" + speaker_ip + ":1400/status/ifconfig"

    # print(f"Speaker IP: {speaker_ip} results in URL: {speaker_bw_url}")

    # Make the request
    # Add Retry? No. Just fail. It can try again on the next collection.
    start = time.time()
    response = requests.get(speaker_bw_url)

    if not response.ok:
        # If the speaker didn't respond OK we have nothing to log
        # just exit to stop data from going to influx
        # could consider just logging the response TIME instead of exiting
        sys.exit(1)

    # Wall clock time of the complete request and response with payload in milliseconds
    total_time = int((time.time() - start) * 1000)

    # Time to first byte according to Python requests, in milliseconds
    elapsed_time = int(response.elapsed.total_seconds() * 1000)

    # Print the status code. Check this later
    # print(response)
    # print(response.content)

    # print(
    #     f"Response.elapsed was {elapsed_time} milliseconds and total was {total_time} milliseconds"
    # )

    # Process the response and update sample_dict{}
    sample_dict = {}
    
    # Have to pass the dictionary, maybe because of comprehension in parse?
    sample_dict = parse_html(response, sample_dict)

    # Add our response time measurements to the samples to estimate router / net health
    sample_dict["total_time"] = total_time
    sample_dict["elapsed_time"] = elapsed_time

    """
    Now we have a sample_dict{} that has everything we want. Have to pass this to influxdb
    Add measurement names and tags.
    """

    # print("Sample Dictionary")
    # print(sample_dict)

    measurement = {}
    # Simple measurement name - usually "net"
    measurement["measurement"] = influx_measurement
    # Simple tags for host and region. Could make this more flexible later if needed.
    measurement["tags"] = {"host": speaker_host, "region": speaker_region}
    # Dictionary of fields and their values. Not used in my line protocol build approach
    # measurement["fields"] = sample_dict

    # Now build first part of line protocol from the dictionary.

    line_body = (
        measurement["measurement"]
        + ",host="
        + measurement["tags"]["host"]
        + ",region="
        + measurement["tags"]["region"]
        + " "
    )

    # print("This is the first half of line format version.")
    # print(line_body)

    # print("Calling append_fields")
    line_body = append_fields(line_body, sample_dict)
    # print(line_body)

    # 1.8 client
    # client = InfluxDBClient(influx_ip, influx_port, influx_user, influx_pass, influx_db, ssl=True, timeout=1, retries=3)

    # 1.8 client
    # Let's write line protocol instead.
    # client.write_points(line_body, protocol="line")

    # setup urllib retries to send to InfluxDB
    retries = urllib3.Retry(connect=3, read=2, redirect=3)

    # 2.0 client from example
    with InfluxDBClient.from_config_file("config.toml", retries=retries) as client:
        with client.write_api(write_options=SYNCHRONOUS) as writer:
            writer.write(bucket=influx_bucket, record=line_body)


if __name__ == "__main__":
    main()
