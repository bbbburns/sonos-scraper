"""
Discover Sonos speakers on the local network and generate a config.toml.

Discovery uses SSDP multicast (canonical, no subnet needed). An optional
TCP port scan on port 1400 can be added with --subnet for stubborn speakers.
Zone names and bonding roles are fetched via UPnP SOAP calls.
"""
import argparse
import ipaddress
import os
import re
import socket
import sys
import time
import toml
import urllib3
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_MX = 2
SSDP_ST = "urn:schemas-upnp-org:device:ZonePlayer:1"

SSDP_REQUEST = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
    "MAN: \"ssdp:discover\"\r\n"
    f"MX: {SSDP_MX}\r\n"
    f"ST: {SSDP_ST}\r\n"
    "\r\n"
)

SOAP_ZONE_ATTRIBUTES = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:GetZoneAttributes xmlns:u="urn:schemas-upnp-org:service:DeviceProperties:1">
    </u:GetZoneAttributes>
  </s:Body>
</s:Envelope>"""

SOAP_ZONE_GROUP_STATE = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:GetZoneGroupState xmlns:u="urn:schemas-upnp-org:service:ZoneGroupTopology:1">
    </u:GetZoneGroupState>
  </s:Body>
</s:Envelope>"""

# HTSatChanMapSet channel codes → host-tag suffix (None = main/coordinator unit)
_CHANNEL_SUFFIXES = {
    "SW": "sub",
    "LR": "left-surround",
    "RR": "right-surround",
    "LF": "left",
    "RF": "right",
}


def ssdp_discover(timeout=3):
    """Send an SSDP M-SEARCH and collect IPs of responding Sonos speakers."""
    ips = set()
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(timeout)
        for _ in range(3):
            sock.sendto(SSDP_REQUEST.encode(), (SSDP_ADDR, SSDP_PORT))
            time.sleep(0.3)
        while True:
            try:
                _, addr = sock.recvfrom(4096)
                ips.add(addr[0])
            except socket.timeout:
                break
    except OSError as e:
        print(f"WARNING: SSDP discovery failed: {e}", file=sys.stderr)
    finally:
        if sock:
            sock.close()
    return ips


def _probe_port(ip, timeout):
    try:
        with socket.create_connection((ip, 1400), timeout=timeout):
            return ip
    except OSError:
        return None


def tcp_scan(subnet, timeout=1):
    """TCP-connect scan port 1400 across all hosts in the given CIDR."""
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError as e:
        print(f"WARNING: invalid subnet '{subnet}': {e}", file=sys.stderr)
        return set()

    if network.num_addresses > 4096:
        print(f"ERROR: subnet '{subnet}' is too large to scan ({network.num_addresses} addresses). Use a /20 or smaller.", file=sys.stderr)
        sys.exit(1)

    ips = set()
    hosts = list(network.hosts())
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(_probe_port, str(h), timeout): str(h) for h in hosts}
        for future in as_completed(futures):
            result = future.result()
            if result:
                ips.add(result)
    return ips


def _channel_role_suffix(channels_str):
    """
    Parse an HTSatChanMapSet channel string and return a host-tag suffix, or None.

    'LF,RF' → None (main/coordinator unit — no suffix)
    'SW'    → 'sub'
    'LR'    → 'left-surround'
    'RR'    → 'right-surround'
    'LF'    → 'left'
    'RF'    → 'right'
    """
    channels = {c.strip() for c in channels_str.split(",")}
    if "LF" in channels and "RF" in channels:
        return None  # coordinator/soundbar — primary unit for the zone
    for code, suffix in _CHANNEL_SUFFIXES.items():
        if code in channels:
            return suffix
    return None


def fetch_zone_topology(ip, timeout=5):
    """
    Fetch full zone group state from one speaker and return info for all members.

    Returns dict of {ip: {'name': str, 'role': str|None}}, or None on failure.
    'role' is a host-tag suffix like 'sub', 'left-surround', 'right-surround',
    or None for standalone speakers and the primary (coordinator) unit in a group.
    """
    url = f"http://{ip}:1400/ZoneGroupTopology/Control"
    try:
        response = requests.post(
            url,
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": '"urn:schemas-upnp-org:service:ZoneGroupTopology:1#GetZoneGroupState"',
            },
            data=SOAP_ZONE_GROUP_STATE,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"WARNING: could not fetch zone topology from {ip}: {e}", file=sys.stderr)
        return None

    try:
        root = ET.fromstring(response.content)
        state_text = root.findtext(".//ZoneGroupState")
        if not state_text:
            return None
        state = ET.fromstring(state_text)
    except ET.ParseError as e:
        print(f"WARNING: failed to parse zone topology from {ip}: {e}", file=sys.stderr)
        return None

    result = {}
    for group in state.findall(".//ZoneGroup"):
        # Satellites (bonded sub/surrounds) are nested inside ZoneGroupMember,
        # not listed as separate top-level members.
        all_members = group.findall("ZoneGroupMember") + group.findall("ZoneGroupMember/Satellite")
        for member in all_members:
            location = member.get("Location", "")
            zone_name = member.get("ZoneName", "")
            uuid = member.get("UUID", "")
            chan_map = member.get("HTSatChanMapSet", "")

            m = re.search(r"http://([^:]+):\d+/", location)
            if not m:
                continue
            member_ip = m.group(1)

            # Parse HTSatChanMapSet: "RINCON_A:LF,RF;RINCON_B:SW;RINCON_C:LR;RINCON_D:RR"
            role_by_rincon = {}
            if chan_map:
                for entry in chan_map.split(";"):
                    parts = entry.split(":", 1)
                    if len(parts) == 2:
                        rincon, channels = parts
                        role_by_rincon[rincon.strip()] = _channel_role_suffix(channels)

            # If uuid is in the map use its role; otherwise None (standalone)
            role = role_by_rincon.get(uuid)

            result[member_ip] = {"name": zone_name, "role": role}

    return result if result else None


def fetch_zone_name(ip, timeout=5):
    """Fallback: fetch just the zone name for a single speaker via SOAP."""
    url = f"http://{ip}:1400/DeviceProperties/Control"
    try:
        response = requests.post(
            url,
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": '"urn:schemas-upnp-org:service:DeviceProperties:1#GetZoneAttributes"',
            },
            data=SOAP_ZONE_ATTRIBUTES,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"WARNING: could not fetch zone name for {ip}: {e}", file=sys.stderr)
        return None

    try:
        root = ET.fromstring(response.content)
        name = root.findtext(".//CurrentZoneName")
        return name.strip() if name else None
    except ET.ParseError as e:
        print(f"WARNING: failed to parse zone attributes response for {ip}: {e}", file=sys.stderr)
        return None


def make_host(name, role=None):
    """Build a host tag from a zone name and optional role suffix."""
    s = name.lower()
    s = s.replace("&", "and")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    s = re.sub(r"-+", "-", s)
    if not s:
        return ""
    if role:
        return f"{s}-{role}-sonos"
    return f"{s}-sonos"


def render_toml(speakers, existing_influx2=None):
    """Render the [[speakers]] array-of-tables and [influx2] block as a TOML string."""
    lines = []
    for speaker in speakers:
        lines.append("[[speakers]]")
        lines.append(f'ip = "{speaker["ip"]}"')
        lines.append(f'host = "{speaker["host"]}"')
        lines.append(f'region = "{speaker["region"]}"')
        lines.append("")

    if existing_influx2:
        lines.append("[influx2]")
        for key, value in existing_influx2.items():
            if isinstance(value, bool):
                lines.append(f"{key} = {str(value).lower()}")
            elif isinstance(value, str):
                lines.append(f'{key} = "{value}"')
            else:
                lines.append(f"{key} = {value}")
    else:
        lines.append("[influx2]")
        lines.append('url = "https://YOUR_INFLUXDB_IP:8086"')
        lines.append('org = "your-org"')
        lines.append('token = "your-token"')
        lines.append("verify_ssl = false")
        lines.append('bucket = "your-bucket"')
        lines.append('measurement = "net"')

    lines.append("")
    return "\n".join(lines)


def load_existing_influx2(path):
    """Load and return the [influx2] section from an existing config file, or None."""
    try:
        config = toml.load(path)
        return config.get("influx2")
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Discover Sonos speakers and generate a config.toml."
    )
    parser.add_argument(
        "--subnet",
        help="CIDR range for TCP port scan fallback (e.g. 192.168.1.0/24)",
    )
    parser.add_argument(
        "--region",
        default="us-east",
        help="Region tag for all discovered speakers (default: us-east)",
    )
    parser.add_argument(
        "--output",
        help="Write config to this file (prints to stdout if omitted)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="SSDP discovery timeout in seconds (default: 3)",
    )
    args = parser.parse_args()

    print("Discovering Sonos speakers via SSDP...", file=sys.stderr)
    ips = ssdp_discover(timeout=args.timeout)

    if args.subnet:
        print(f"Running TCP scan on {args.subnet}...", file=sys.stderr)
        ips |= tcp_scan(args.subnet)

    if not ips:
        print("ERROR: no Sonos speakers found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(ips)} speaker(s). Fetching zone topology...", file=sys.stderr)

    # Try to get full topology (zone names + bonding roles) from any one speaker.
    topology = None
    for ip in ips:
        topology = fetch_zone_topology(ip)
        if topology:
            break

    # Fall back to individual zone-name lookups if topology fetch fails.
    if topology is None:
        print("WARNING: topology fetch failed, falling back to per-speaker zone names.", file=sys.stderr)
        name_map = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_zone_name, ip): ip for ip in ips}
            for future in as_completed(futures):
                name_map[futures[future]] = future.result()
        topology = {ip: {"name": name_map.get(ip), "role": None} for ip in ips}

    existing_influx2 = None
    if args.output and os.path.exists(args.output):
        print(
            f"WARNING: {args.output} already exists — preserving existing [influx2] section.",
            file=sys.stderr,
        )
        existing_influx2 = load_existing_influx2(args.output)

    speakers = []
    for ip in sorted(ips, key=lambda x: ipaddress.ip_address(x)):
        info = topology.get(ip, {})
        name = info.get("name")
        role = info.get("role")
        if name:
            host = make_host(name, role)
        else:
            host = ip.replace(".", "-") + "-sonos"
        speakers.append({"ip": ip, "host": host, "region": args.region})

    output = render_toml(speakers, existing_influx2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Config written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
