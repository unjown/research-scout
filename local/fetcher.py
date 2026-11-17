"""Conservative direct HTTP retrieval of public pages; never uses search credits."""
import ipaddress
import socket
from urllib.parse import urlsplit
import requests
from bs4 import BeautifulSoup


def fetch_excerpt(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.port not in (None, 80, 443):
            return ""
        host = parsed.hostname.lower()
        if host == "localhost" or host.endswith(".local"):
            return ""
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            return ""
        # Requests resolves again; DNS rebinding remains a residual risk. No redirects,
        # no credentials, and no privileged local network access are permitted.
        with requests.get(url, timeout=(4, 12), stream=True, allow_redirects=False,
                          headers={"User-Agent": "ResearchScout/0.1 (+personal research; respectful fetch)"}) as r:
            if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type", "").lower():
                return ""
            raw = bytearray()
            for chunk in r.iter_content(chunk_size=8192):
                raw.extend(chunk)
                if len(raw) > 350_000:
                    break
            html = raw.decode(r.encoding or "utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            element.decompose()
        return soup.get_text(" ", strip=True)[:3000]
    except (requests.RequestException, OSError, ValueError):
        return ""
