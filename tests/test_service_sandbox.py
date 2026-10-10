"""
The bot's systemd unit must keep it on the public internet.

`!play` hands user links to yt-dlp, which follows redirects and resolves DNS by
itself, so services.media.is_public_url cannot stop a link from reaching
localhost or the LAN of the machine the bot runs on. The unit's IPAddressDeny
does, in the kernel. These tests read the list out of the deploy script and
check it against addresses on both sides of the line.
"""

import ipaddress
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "install-units.sh"

# What the bot must never reach: the host itself, its LAN and containers, cloud
# metadata, carrier-grade NAT (VPN overlays) and their IPv6 equivalents.
PRIVATE = [
    "0.0.0.0", "127.0.0.1", "127.0.0.53", "::1",
    "10.0.0.1", "172.16.0.1", "172.20.0.1", "172.31.250.1", "192.168.1.1",
    "169.254.169.254", "fe80::1", "100.64.0.1", "100.100.100.100",
    "198.18.0.1", "224.0.0.1", "ff02::1", "fd00::1", "fc00::1",
]

# What it needs: Discord, YouTube, public DNS, over IPv4 and IPv6.
PUBLIC = [
    "162.159.136.232", "142.250.80.46", "1.1.1.1", "8.8.8.8",
    "2606:4700::1111", "2607:f8b0:4004:c07::64",
]


def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def deny_list() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    match = re.search(r'^PRIVATE_NETWORKS="([^"]+)"', script(), re.MULTILINE)
    assert match, "PRIVATE_NETWORKS is not set in deploy/install-units.sh"
    return [ipaddress.ip_network(prefix) for prefix in match.group(1).split()]


def bot_unit() -> str:
    blocks = re.findall(r"\$\{SERVICE_NAME\}\.service\" >/dev/null <<UNIT\n(.*?)\nUNIT",
                        script(), re.DOTALL)
    assert len(blocks) == 1, "the loopify-bot unit was not found"
    return blocks[0]


def denied(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return any(ip.version == net.version and ip in net for net in deny_list())


@pytest.mark.parametrize("address", PRIVATE)
def test_every_private_address_is_denied(address):
    assert denied(address), f"{address} is reachable from the bot"


@pytest.mark.parametrize("address", PUBLIC)
def test_no_public_address_is_denied(address):
    assert not denied(address), f"{address} would be blocked, and the bot needs it"


def test_the_bot_unit_applies_the_list():
    unit = bot_unit()
    assert "IPAddressDeny=$PRIVATE_NETWORKS" in unit.splitlines()


def test_only_the_dns_resolvers_are_let_back_in():
    """Anything else in IPAddressAllow would reopen a private address."""
    allows = [line for line in bot_unit().splitlines() if line.startswith("IPAddressAllow=")]
    assert allows == ["IPAddressAllow=$RESOLVERS"]
    assert re.search(r'^RESOLVERS="\$\(awk .*/etc/resolv\.conf', script(), re.MULTILINE)
