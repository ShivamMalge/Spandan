"""Synthetic identifier pools, and the reserved ranges they are drawn from.

`agents.md` §7 requires that every identifier in this project is synthetic: no
real BINs, no real card numbers, no real IPs. That is not satisfied by "we made
these up" — it is satisfied by drawing from ranges that are *reserved by standard*
and therefore cannot collide with anything real. Each range below is named with
its standard so the choice is auditable, and `tests/test_gen.py` asserts every
generated identifier falls inside one.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

import numpy as np

# --- BINs -----------------------------------------------------------------

#: First digit of every synthetic BIN.
#:
#: ISO/IEC 7812 assigns the Major Industry Identifier — the leading digit of an
#: issuer identification number — by industry. MII 0 is reserved to ISO/TC 68 and
#: is not issued to card schemes; every live scheme sits at 1-6 (Visa 4,
#: Mastercard 2/5, Amex 3, Discover 6, RuPay 6). A BIN beginning "0" therefore
#: cannot be a real issuer's.
SYNTHETIC_BIN_MII = "0"

BIN_LENGTH = 6


def make_bins(rng: np.random.Generator, count: int) -> list[str]:
    """`count` distinct synthetic BINs, all in the reserved MII-0 space."""
    body_width = BIN_LENGTH - len(SYNTHETIC_BIN_MII)
    universe = 10**body_width
    if count > universe:
        raise ValueError(f"cannot draw {count} distinct BINs from {universe}")
    bodies = rng.choice(universe, size=count, replace=False)
    return [f"{SYNTHETIC_BIN_MII}{int(b):0{body_width}d}" for b in sorted(bodies)]


def is_synthetic_bin(value: str) -> bool:
    return (
        len(value) == BIN_LENGTH
        and value.isdigit()
        and value.startswith(SYNTHETIC_BIN_MII)
    )


# --- IPv4 -----------------------------------------------------------------

#: Reserved IPv4 blocks, with the standard that reserves each. None of these is
#: routable on the public internet, so no generated address can correspond to a
#: real host.
RESERVED_IPV4_BLOCKS: tuple[tuple[str, str], ...] = (
    ("192.0.2.0/24", "RFC 5737 TEST-NET-1"),
    ("198.51.100.0/24", "RFC 5737 TEST-NET-2"),
    ("203.0.113.0/24", "RFC 5737 TEST-NET-3"),
    ("198.18.0.0/15", "RFC 2544 benchmarking range"),
)

_NETWORKS = tuple(ipaddress.IPv4Network(cidr) for cidr, _ in RESERVED_IPV4_BLOCKS)
_NETWORK_SIZES = tuple(net.num_addresses for net in _NETWORKS)
_TOTAL_ADDRESSES = sum(_NETWORK_SIZES)


def make_ips(rng: np.random.Generator, count: int) -> list[str]:
    """`count` distinct synthetic IPv4 addresses from the reserved blocks."""
    if count > _TOTAL_ADDRESSES:
        raise ValueError(f"cannot draw {count} distinct IPs from {_TOTAL_ADDRESSES}")
    picks = rng.choice(_TOTAL_ADDRESSES, size=count, replace=False)
    out = []
    for index in sorted(int(p) for p in picks):
        for net, size in zip(_NETWORKS, _NETWORK_SIZES):
            if index < size:
                out.append(str(net[index]))
                break
            index -= size
    return out


def is_synthetic_ip(value: str) -> bool:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    return any(address in net for net in _NETWORKS)


# --- opaque references ----------------------------------------------------
#
# Cards and devices are referred to by opaque synthetic tokens. There is no card
# number anywhere in this project: nothing here is a PAN, derived from a PAN, or
# Luhn-valid, and the detector never needs one — per-card velocity only requires
# that the same card is recognisable as the same card.

CARD_REF_PREFIX = "card_"
DEVICE_ID_PREFIX = "dev_"
_TOKEN_WIDTH = 10


def _tokens(prefix: str, count: int, offset: int = 0) -> list[str]:
    """Sequential opaque tokens.

    Deliberately not randomised. The detector treats these as opaque keys — the
    only property it needs is that the same card is recognisable as the same card
    — so randomising them would buy no realism and would cost a large-universe
    distinct-sample draw for nothing. `offset` keeps separate populations (benign
    cards, per-episode scenario cards) from colliding.
    """
    if count < 0:
        raise ValueError("count must be non-negative")
    end = offset + count
    if end > 10**_TOKEN_WIDTH:
        raise ValueError(f"token space exhausted at {end}")
    return [f"{prefix}{i:0{_TOKEN_WIDTH}d}" for i in range(offset, end)]


def make_card_refs(count: int, offset: int = 0) -> list[str]:
    return _tokens(CARD_REF_PREFIX, count, offset)


def make_device_ids(count: int, offset: int = 0) -> list[str]:
    return _tokens(DEVICE_ID_PREFIX, count, offset)


def is_synthetic_card_ref(value: str) -> bool:
    body = value.removeprefix(CARD_REF_PREFIX)
    return value.startswith(CARD_REF_PREFIX) and body.isdigit() and len(body) == _TOKEN_WIDTH


def is_synthetic_device_id(value: str) -> bool:
    body = value.removeprefix(DEVICE_ID_PREFIX)
    return value.startswith(DEVICE_ID_PREFIX) and body.isdigit() and len(body) == _TOKEN_WIDTH


# --- merchants ------------------------------------------------------------

MERCHANT_PREFIX = "mer_"


@dataclass(frozen=True, slots=True)
class Merchant:
    merchant_id: str
    base_hourly_rate: float
    """Mean transactions per hour before the diurnal multiplier."""
    decline_rate: float
    """This merchant's benign decline rate."""
    amount_median_paise: int
    amount_log_sigma: float


def make_merchant_ids(count: int) -> list[str]:
    return [f"{MERCHANT_PREFIX}{i:03d}" for i in range(count)]
