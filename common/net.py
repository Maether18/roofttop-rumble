"""Small address helpers shared by the servers and clients (no game logic)."""
import socket
import threading
import urllib.request

PUBLIC_IP_URL = "https://api.ipify.org"


def resolve_host(host):
    """Hostname or IP -> IPv4 address string. Raises socket.gaierror if unknown.
    IPv4 only, because the servers listen on IPv4 (otherwise "localhost"
    may resolve to ::1 first and the connection is refused)."""
    return socket.getaddrinfo(host.strip(), None, socket.AF_INET)[0][4][0]


def local_ip():
    """Best guess of this computer's LAN IP, to show clients where to connect."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))        # no packet is sent; it only picks a route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def public_ip(timeout=3.0):
    """This network's public IP as seen from the internet, or None if the
    lookup fails or takes longer than timeout (for example when offline)."""
    result = []

    def lookup():
        try:
            with urllib.request.urlopen(PUBLIC_IP_URL, timeout=timeout) as r:
                result.append(r.read().decode().strip())
        except Exception:
            pass

    t = threading.Thread(target=lookup, daemon=True)  # DNS lookups ignore the timeout
    t.start()
    t.join(timeout)
    return result[0] if result else None


def print_addresses(protocol, bind_host, port):
    """Startup banner: where LAN and internet clients should connect."""
    print(f"{protocol} server listening on {bind_host}:{port}")
    print(f"  LAN clients:      --host {local_ip()} --port {port}")
    ip = public_ip()
    if ip:
        print(f"  Internet clients: --host {ip} --port {port}"
              f"  (needs port forwarding, see README 'Cross-network play')")
    else:
        print("  Public IP: lookup failed (offline or blocked). LAN play still works.")
