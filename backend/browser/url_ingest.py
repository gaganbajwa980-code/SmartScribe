import socket
import ipaddress
import urllib.parse
import http.client
from typing import Dict, Any, Tuple, Optional

from backend.browser.context import BrowserPageContext
from backend.browser.extractor import extract_article_from_html

MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 8.0
MAX_RESPONSE_BYTES = 3 * 1024 * 1024  # 3 MB limit

ALLOWED_SCHEMES = {"http", "https"}

BLOCKED_HOSTNAMES = {
    "localhost", "127.0.0.1", "::1", "0.0.0.0",
    "metadata.google.internal", "169.254.169.254"
}

class SSRFSecurityException(Exception):
    """Raised when a URL attempts to connect to a local or private network address."""
    pass

class InvalidSchemeException(Exception):
    """Raised when URL scheme is not http or https."""
    pass

class OversizedContentException(Exception):
    """Raised when downloaded response exceeds maximum safe size."""
    pass


def validate_url_and_ip(url: str) -> Tuple[str, str, int, str]:
    """
    Validates URL scheme and performs DNS resolution to enforce SSRF defenses.
    Rejects loopback, private IPv4/IPv6, link-local, multicast, and reserved addresses.
    Returns:
      (scheme, hostname, port, resolved_ip)
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL must be a non-empty string.")

    parsed = urllib.parse.urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()

    if scheme not in ALLOWED_SCHEMES:
        raise InvalidSchemeException(f"Unsupported URL scheme '{scheme}'. Only http:// and https:// are permitted.")

    hostname = (parsed.hostname or "").lower().strip()
    if not hostname:
        raise ValueError("URL does not contain a valid hostname.")

    # Hostname pattern checks
    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(".localhost") or hostname.endswith(".local") or hostname.endswith(".internal"):
        raise SSRFSecurityException(f"Access to local or private hostname '{hostname}' is blocked.")

    port = parsed.port or (443 if scheme == "https" else 80)

    # Perform DNS resolution
    try:
        addr_infos = socket.getaddrinfo(hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ConnectionError(f"Failed to resolve hostname '{hostname}': {e}")

    if not addr_infos:
        raise ConnectionError(f"No IP addresses resolved for hostname '{hostname}'.")

    # Validate EVERY resolved IP address
    first_resolved_ip = None
    for family, socktype, proto, canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        if first_resolved_ip is None:
            first_resolved_ip = ip_str

        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SSRFSecurityException(f"Invalid resolved IP address '{ip_str}'.")

        if (
            ip.is_loopback or
            ip.is_private or
            ip.is_link_local or
            ip.is_multicast or
            ip.is_reserved or
            ip.is_unspecified
        ):
            raise SSRFSecurityException(
                f"Destination '{hostname}' resolved to restricted/private IP address: {ip_str}"
            )

    return scheme, hostname, port, first_resolved_ip


def fetch_url_content(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_RESPONSE_BYTES
) -> Dict[str, Any]:
    """
    Safely fetches public HTTP/HTTPS page content enforcing SSRF protections,
    timeout limits, response size limits, and cautious redirect validation.
    """
    current_url = url.strip()
    redirects = 0

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SmartScribe/1.0 (Desktop AI Assistant; ReadOnly)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "close"
    }

    while redirects <= MAX_REDIRECTS:
        try:
            scheme, hostname, port, resolved_ip = validate_url_and_ip(current_url)
        except InvalidSchemeException as e:
            return {"success": False, "error_type": "invalid_scheme", "message": str(e), "url": current_url}
        except SSRFSecurityException as e:
            return {"success": False, "error_type": "ssrf_blocked", "message": str(e), "url": current_url}
        except Exception as e:
            return {"success": False, "error_type": "dns_failed", "message": str(e), "url": current_url}

        parsed = urllib.parse.urlparse(current_url)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        # Connect using http.client
        conn = None
        try:
            if scheme == "https":
                import ssl
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(hostname, port=port, timeout=timeout, context=ctx)
            else:
                conn = http.client.HTTPConnection(hostname, port=port, timeout=timeout)

            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            status = response.status

            # Handle Redirects
            if status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    return {"success": False, "error_type": "redirect_error", "message": "Redirect without Location header", "url": current_url}
                redirects += 1
                if redirects > MAX_REDIRECTS:
                    return {"success": False, "error_type": "too_many_redirects", "message": f"Exceeded max redirects ({MAX_REDIRECTS})", "url": current_url}
                # Resolve relative redirect URLs safely
                current_url = urllib.parse.urljoin(current_url, location)
                conn.close()
                continue

            if status != 200:
                conn.close()
                return {"success": False, "error_type": "http_error", "message": f"HTTP response status {status}", "status_code": status, "url": current_url}

            # Verify Content-Length if present
            cl_header = response.getheader("Content-Length")
            if cl_header:
                try:
                    if int(cl_header) > max_bytes:
                        conn.close()
                        return {"success": False, "error_type": "oversized", "message": f"Content-Length {cl_header} exceeds {max_bytes} bytes", "url": current_url}
                except ValueError:
                    pass

            # Stream read up to max_bytes
            content_chunks = []
            total_bytes = 0
            chunk_size = 64 * 1024

            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    conn.close()
                    return {"success": False, "error_type": "oversized", "message": f"Downloaded response exceeded {max_bytes} bytes limit", "url": current_url}
                content_chunks.append(chunk)

            raw_bytes = b"".join(content_chunks)
            conn.close()

            # Attempt clean decoding
            content_type = response.getheader("Content-Type", "")
            encoding = "utf-8"
            if "charset=" in content_type.lower():
                encoding = content_type.lower().split("charset=")[-1].split(";")[0].strip()

            try:
                html_text = raw_bytes.decode(encoding, errors="replace")
            except Exception:
                html_text = raw_bytes.decode("utf-8", errors="replace")

            return {
                "success": True,
                "html": html_text,
                "url": current_url,
                "status_code": 200,
                "bytes_received": total_bytes
            }

        except socket.timeout:
            if conn:
                conn.close()
            return {"success": False, "error_type": "timeout", "message": f"Request timed out after {timeout} seconds", "url": current_url}
        except Exception as e:
            if conn:
                conn.close()
            return {"success": False, "error_type": "fetch_error", "message": str(e), "url": current_url}

    return {"success": False, "error_type": "too_many_redirects", "message": "Exceeded maximum redirects", "url": current_url}


def ingest_public_url(url: str, timeout: float = DEFAULT_TIMEOUT) -> BrowserPageContext:
    """
    High-level entry point for Tier 1 ingestion.
    Fetches URL, enforces security, and returns parsed BrowserPageContext.
    """
    domain = ""
    try:
        domain = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        domain = ""

    fetch_res = fetch_url_content(url, timeout=timeout)

    if not fetch_res.get("success"):
        err_type = fetch_res.get("error_type")
        msg = fetch_res.get("message", "URL fetch failed")

        if err_type in ("ssrf_blocked", "invalid_scheme"):
            status = "unsafe_url"
        elif err_type == "oversized":
            status = "error"
        else:
            status = "empty_unreadable"

        return BrowserPageContext(
            url=url,
            domain=domain,
            title="Extraction Failed",
            extracted_text=f"Failed to ingest URL: {msg}",
            headings=[],
            source_type="url_ingest",
            extraction_status=status,
            word_count=0,
            metadata={"error_type": err_type, "error_message": msg}
        )

    # Successfully retrieved HTML
    final_url = fetch_res.get("url", url)
    html_text = fetch_res.get("html", "")
    return extract_article_from_html(html_text, url=final_url, source_type="url_ingest")
