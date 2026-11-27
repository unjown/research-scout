import ipaddress
import socket
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .cache import get_cached, save_cached


MAX_BYTES = 1_000_000
MAX_REDIRECTS = 3


def validate_url(url):
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Unsupported URL scheme")

    if not parsed.hostname:
        raise ValueError("Missing hostname")

    if parsed.username or parsed.password:
        raise ValueError("Credentials in URL are forbidden")

    if parsed.port and parsed.port not in (80, 443):
        raise ValueError("Unsupported URL port")

    addresses = socket.getaddrinfo(
        parsed.hostname, None, type=socket.SOCK_STREAM
    )

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])

        if not ip.is_global:
            raise ValueError("Private or restricted address blocked")


def fetch_source(url):
    cached = get_cached(url)

    if cached is not None:
        return cached

    session = requests.Session()
    session.trust_env = False

    current = url

    for _ in range(MAX_REDIRECTS + 1):
        validate_url(current)

        with session.get(
            current,
            timeout=(5, 15),
            allow_redirects=False,
            stream=True,
            headers={"User-Agent": "ResearchScout/1.0"},
        ) as response:

            if response.is_redirect:
                location = response.headers.get("Location")

                if not location:
                    raise ValueError("Redirect without location")

                from urllib.parse import urljoin
                current = urljoin(current, location)
                continue

            response.raise_for_status()

            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            if not (
                "text/html" in content_type
                or "text/plain" in content_type
            ):
                raise ValueError("Unsupported content type")

            chunks = []
            size = 0

            for chunk in response.iter_content(16384):
                size += len(chunk)

                if size > MAX_BYTES:
                    raise ValueError("Source exceeds size limit")

                chunks.append(chunk)

            html = b"".join(chunks).decode(
                response.encoding or "utf-8",
                errors="replace",
            )

            if "text/html" in content_type:
                soup = BeautifulSoup(html, "html.parser")

                for element in soup(
                    ["script", "style", "nav", "footer"]
                ):
                    element.decompose()

                content = soup.get_text(" ", strip=True)
            else:
                content = html.strip()

            content = content[:12000]

            save_cached(url, content)
            return content

    raise ValueError("Too many redirects")