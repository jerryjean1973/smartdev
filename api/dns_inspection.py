"""Read DNS delegation and provider records without granting mutation access."""
import hmac
import ipaddress
import re
import subprocess

LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def domain_name(value):
    ascii_name = value.strip().rstrip(".").encode("idna").decode("ascii").lower()
    labels = ascii_name.split(".")
    if not 1 < len(labels) <= 30 or len(ascii_name) > 253 or not all(LABEL.fullmatch(x) for x in labels):
        raise ValueError("Enter a valid DNS zone name.")
    return ascii_name


def delegation(zone):
    result = subprocess.run(
        ["dig", "-r", "+time=2", "+tries=1", "+noall", "+answer", zone + ".", "NS"],
        capture_output=True, text=True, timeout=4,
    )
    if result.returncode != 0:
        raise RuntimeError("DNS delegation lookup is unavailable.")
    return parse_nameservers(zone, result.stdout)


def parse_nameservers(zone, output):
    servers = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 5 and fields[0].rstrip(".").lower() == zone and fields[2:4] == ["IN", "NS"]:
            servers.add(domain_name(fields[4]))
    return sorted(servers)


def matching_zone(zone, public_nameservers, rows):
    if not public_nameservers:
        return None
    public_nameservers = set(public_nameservers)
    matches = []
    for row in rows:
        if row.get("name") != zone or row.get("status") != "active":
            continue
        if set(map(domain_name, row.get("name_servers") or [])) != public_nameservers:
            continue
        if re.fullmatch(r"[a-f0-9]{32}", row.get("id", "")):
            matches.append(row["id"])
    return matches[0] if len(matches) == 1 else None


PREFIX = "_smartdev-verification."


def verification_hostname(value):
    host = domain_name(value)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Enter a hostname instead of an IP address.")
    if len(PREFIX + host) > 253 or host.rsplit(".", 1)[-1].isdigit():
        raise ValueError("Enter a hostname that supports a verification TXT record.")
    return host


def txt_matches(answer, name, token):
    for line in answer.splitlines():
        fields = line.split(None, 4)
        if len(fields) != 5 or fields[0].lower() != name + "." or fields[2:4] != ["IN", "TXT"]:
            continue
        match = re.fullmatch(r'"([A-Za-z0-9_-]+)"', fields[4])
        if match and hmac.compare_digest(match[1], token):
            return True
    return False


def verification_txt_answer(name):
    try:
        result = subprocess.run(
            ["dig", "-r", "+time=2", "+tries=1", "+noall", "+comments", "+answer", name + ".", "TXT"],
            capture_output=True, text=True, timeout=4, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("DNS lookup is unavailable. Try again.") from None
    if not re.search(r"status: (NOERROR|NXDOMAIN),", result.stdout):
        raise RuntimeError("DNS lookup did not return a usable answer.")
    return result.stdout
