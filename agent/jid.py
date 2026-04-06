"""JID utility functions — single source of truth."""
from __future__ import annotations


def jid_to_number(jid: str) -> str:
    """Extract phone number from JID: '12345:2@s.whatsapp.net' → '12345'."""
    return jid.split(":")[0].split("@")[0]


def normalize_jid(jid: str) -> str:
    """Strip device suffix, preserving domain: '12345:2@s.whatsapp.net' → '12345@s.whatsapp.net'.
    Group JIDs (@g.us) are returned as-is."""
    if is_group_jid(jid):
        return jid
    return f"{jid_to_number(jid)}@s.whatsapp.net"


def number_to_jid(number: str) -> str:
    """Convert phone number to JID."""
    digits = "".join(c for c in number if c.isdigit())
    return f"{digits}@s.whatsapp.net"


def is_group_jid(jid: str) -> bool:
    """Check if a JID is a group chat."""
    return jid.endswith("@g.us")


def same_user(jid1: str, jid2: str) -> bool:
    """Compare JIDs ignoring device suffix."""
    return jid_to_number(jid1) == jid_to_number(jid2)
