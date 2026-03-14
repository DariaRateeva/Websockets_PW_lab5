import sys
import socket
import ssl
from urllib.parse import urlparse

from bs4 import BeautifulSoup

def make_raw_request(url):
    """
    Perform an HTTP/1.1 GET request using only raw sockets.
    Handles HTTPS via ssl wrapping.
    Returns (headers_dict, body_string).
    """
    parsed = urlparse(url)
    scheme = parsed.scheme or "http"
    host = parsed.hostname
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    # --- Build raw HTTP request ---
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"User-Agent: go2web/1.0\r\n"
        f"Accept: text/html, */*\r\n"
        f"Accept-Encoding: identity\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )

    # --- Open TCP socket ---
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(15)

    try:
        if scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        sock.connect((host, port))
        sock.sendall(request.encode("utf-8"))

        # --- Receive full response ---
        response = b""
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            except socket.timeout:
                break
    finally:
        sock.close()

    # --- Split headers and body ---
    header_end = response.find(b"\r\n\r\n")
    if header_end == -1:
        return {}, response.decode("utf-8", errors="replace")

    raw_headers = response[:header_end].decode("utf-8", errors="replace")
    raw_body = response[header_end + 4:]

    # --- Parse status line ---
    header_lines = raw_headers.split("\r\n")

    # --- Parse headers into dict ---
    headers = {}
    for line in header_lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    # --- Handle chunked transfer encoding ---
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = _decode_chunked(raw_body)
    else:
        body = raw_body

    body_str = body.decode("utf-8", errors="replace")
    return headers, body_str


def _decode_chunked(raw):
    """Decode HTTP chunked transfer encoding."""
    decoded = b""
    data = raw
    while True:
        line_end = data.find(b"\r\n")
        if line_end == -1:
            break
        size_str = data[:line_end].decode("utf-8").strip()
        if not size_str:
            break
        try:
            chunk_size = int(size_str, 16)
        except ValueError:
            break
        if chunk_size == 0:
            break
        chunk_start = line_end + 2
        chunk_end = chunk_start + chunk_size
        decoded += data[chunk_start:chunk_end]
        data = data[chunk_end + 2:]  # skip trailing \r\n
    return decoded


# ===========================================================================
# CONTENT RENDERING
# ===========================================================================

def render_response(headers, body):
    """
    Render the response body as human-readable text.
    Strips HTML tags using BeautifulSoup.
    """
    content_type = headers.get("content-type", "")

    # --- HTML response ---
    if "text/html" in content_type or "<html" in body.lower()[:200]:
        soup = BeautifulSoup(body, "html.parser")
        # Remove script and style elements
        for tag in soup(["script", "style", "noscript", "iframe"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # Collapse multiple blank lines
        lines = [line for line in text.splitlines() if line.strip()]
        print("\n" + "\n".join(lines))
        return

    # --- Plain text or other ---
    print(f"\n{body}")


# ===========================================================================
# CLI
# ===========================================================================

HELP_TEXT = """\
go2web - A command-line HTTP client (raw sockets)

Usage:
  go2web -u <URL>          Make an HTTP request to the URL and print the response
  go2web -s <search-term>  Search the term and print top 10 results
  go2web -h                Show this help

Examples:
  go2web -u https://example.com
  go2web -s "python sockets tutorial"
"""


def main():
    args = sys.argv[1:]

    if not args or args[0] == "-h" or args[0] == "--help":
        print(HELP_TEXT)
        return

    if args[0] == "-u":
        if len(args) < 2:
            print("Error: -u requires a URL argument.")
            print("Usage: go2web -u <URL>")
            sys.exit(1)
        url = args[1]
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        print(f"Fetching: {url}")
        headers, body = make_raw_request(url)
        render_response(headers, body)

    elif args[0] == "-s":
        if len(args) < 2:
            print("Error: -s requires a search term.")
            print("Usage: go2web -s <search-term>")
            sys.exit(1)
        term = " ".join(args[1:])
        print(f"TODO: search for \"{term}\"")

    else:
        print(f"Unknown option: {args[0]}")
        print(HELP_TEXT)
        sys.exit(1)


if __name__ == "__main__":
    main()