"""SSRF guard shared by everything that downloads a URL we didn't write ourselves."""
import ipaddress


def is_public_ip(ip: str) -> bool:
    """False for loopback, private, link-local, reserved... addresses."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    mapped = getattr(a, "ipv4_mapped", None)
    if mapped is not None:
        a = mapped
    return a.is_global
