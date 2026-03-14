import sys
import socket
import ssl
import json
import os
import hashlib
import time
from urllib.parse import urlparse, quote_plus, parse_qs

from bs4 import BeautifulSoup



# CACHE
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".go2web_cache")
CACHE_INDEX = os.path.join(CACHE_DIR, "index.json")


def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not os.path.exists(CACHE_INDEX):
        with open(CACHE_INDEX, "w") as f:
            json.dump({}, f)


def _load_cache_index():
    _ensure_cache_dir()
    with open(CACHE_INDEX, "r") as f:
        return json.load(f)


def _save_cache_index(index):
    _ensure_cache_dir()
    with open(CACHE_INDEX, "w") as f:
        json.dump(index, f, indent=2)


def _url_hash(url):
    return hashlib.sha256(url.encode()).hexdigest()


def cache_get(url):
    """Return cached (headers_dict, body) and cache metadata, or None."""
    index = _load_cache_index()
    key = _url_hash(url)
    if key not in index:
        return None
    entry = index[key]
    body_path = os.path.join(CACHE_DIR, key + ".body")
    if not os.path.exists(body_path):
        return None
    with open(body_path, "r", encoding="utf-8", errors="replace") as f:
        body = f.read()
    return entry, body


def cache_put(url, headers, body):
    """Store response in cache."""
    index = _load_cache_index()
    key = _url_hash(url)
    entry = {"url": url, "time": time.time()}
    if "etag" in headers:
        entry["etag"] = headers["etag"]
    if "last-modified" in headers:
        entry["last-modified"] = headers["last-modified"]
    index[key] = entry
    _save_cache_index(index)
    body_path = os.path.join(CACHE_DIR, key + ".body")
    with open(body_path, "w", encoding="utf-8", errors="replace") as f:
        f.write(body)


# RAW HTTP REQUEST via TCP SOCKET
def make_raw_request(url, max_redirects=10, use_cache=True):
    """
    Perform an HTTP/1.1 GET request using only raw sockets.
    Handles HTTPS, redirects, chunked encoding, cache, and content negotiation.
    Returns (headers_dict, body_string, final_url).
    """
    for redirect_num in range(max_redirects + 1):
        parsed = urlparse(url)
        scheme = parsed.scheme or "http"
        host = parsed.hostname
        port = parsed.port or (443 if scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        # --- Check cache (conditional GET) ---
        cached = cache_get(url) if use_cache else None
        conditional_headers = ""
        if cached:
            meta, cached_body = cached
            if "etag" in meta:
                conditional_headers += f"If-None-Match: {meta['etag']}\r\n"
            if "last-modified" in meta:
                conditional_headers += f"If-Modified-Since: {meta['last-modified']}\r\n"

        # --- Build raw HTTP request with content negotiation ---
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: go2web/1.0\r\n"
            f"Accept: application/json, text/html;q=0.9, */*;q=0.8\r\n"
            f"Accept-Encoding: identity\r\n"
            f"Connection: close\r\n"
            f"{conditional_headers}"
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
            return {}, response.decode("utf-8", errors="replace"), url

        raw_headers = response[:header_end].decode("utf-8", errors="replace")
        raw_body = response[header_end + 4:]

        # --- Parse status line ---
        header_lines = raw_headers.split("\r\n")
        status_line = header_lines[0]
        status_code = int(status_line.split()[1])

        # --- Parse headers into dict ---
        headers = {}
        for line in header_lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        # --- Handle 304 Not Modified (cache hit) ---
        if status_code == 304 and cached:
            print(f"  [cache hit for {host}{path}]")
            _, cached_body = cached
            return headers, cached_body, url

        # --- Handle chunked transfer encoding ---
        if headers.get("transfer-encoding", "").lower() == "chunked":
            body = _decode_chunked(raw_body)
        else:
            body = raw_body

        body_str = body.decode("utf-8", errors="replace")

        # --- Handle redirects (301, 302, 303, 307, 308) ---
        if status_code in (301, 302, 303, 307, 308):
            location = headers.get("location", "")
            if not location:
                break
            # Handle relative redirects
            if location.startswith("/"):
                location = f"{scheme}://{host}{location}"
            elif not location.startswith("http"):
                location = f"{scheme}://{host}/{location}"
            print(f"  [redirect {status_code} -> {location}]")
            url = location
            continue

        # --- Store in cache ---
        if use_cache and status_code == 200:
            cache_put(url, headers, body_str)

        return headers, body_str, url

    # If we exhausted redirects
    return headers, body_str, url


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


# CONTENT RENDERING
def render_response(headers, body):
    """
    Render the response body as human-readable text.
    Handles both JSON and HTML content types (content negotiation).
    """
    content_type = headers.get("content-type", "")

    # --- JSON response ---
    if "application/json" in content_type:
        try:
            data = json.loads(body)
            print("\n[Content-Type: JSON]\n")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return
        except json.JSONDecodeError:
            pass  # Fall through to HTML/text handling

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


# SEARCH
def search(term):
    """
    Search using DuckDuckGo (HTML version) and return top 10 results.
    Returns list of (title, url) tuples.
    """
    query = quote_plus(term)
    search_url = f"https://html.duckduckgo.com/html/?q={query}"

    headers, body, _ = make_raw_request(search_url, use_cache=False)

    soup = BeautifulSoup(body, "html.parser")
    results = []

    for link in soup.select("a.result__a"):
        title = link.get_text(strip=True)
        href = link.get("href", "")
        # DuckDuckGo wraps URLs — extract the actual URL
        if "uddg=" in href:
            parsed_href = urlparse(href)
            qs = parse_qs(parsed_href.query)
            if "uddg" in qs:
                href = qs["uddg"][0]
        if title and href and href.startswith("http"):
            results.append((title, href))
        if len(results) >= 10:
            break

    return results


def display_search_results(results):
    """Print search results in a numbered list."""
    if not results:
        print("\nNo results found.")
        return
    print(f"\nTop {len(results)} results:\n")
    for i, (title, url) in enumerate(results, 1):
        print(f"  {i}. {title}")
        print(f"     {url}\n")


def interactive_search(term):
    """
    Search and allow the user to pick a result to open.
    Bonus: clickable results via CLI.
    """
    results = search(term)
    display_search_results(results)

    if not results:
        return

    print("Enter a result number to open it, or 'q' to quit:")
    while True:
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice.lower() in ("q", "quit", "exit", ""):
            break
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(results):
                title, url = results[idx]
                print(f"\nFetching: {url}\n" + "-" * 60)
                headers, body, final_url = make_raw_request(url)
                render_response(headers, body)
                print("\n" + "-" * 60)
                print("\nEnter another number, or 'q' to quit:")
            else:
                print(f"Please enter a number between 1 and {len(results)}.")
        except ValueError:
            print("Invalid input. Enter a number or 'q'.")


# CLI
HELP_TEXT = """\
go2web - A command-line HTTP client (raw sockets)

Usage:
  go2web -u <URL>          Make an HTTP request to the URL and print the response
  go2web -s <search-term>  Search the term and print top 10 results
  go2web -h                Show this help

Features:
  - HTTP and HTTPS support
  - Automatic redirect following
  - HTTP caching (ETag / Last-Modified)
  - Content negotiation (JSON + HTML)
  - Interactive search result navigation

Examples:
  go2web -u https://example.com
  go2web -s "python sockets tutorial"
  go2web -u https://api.github.com/repos/torvalds/linux
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
        headers, body, final_url = make_raw_request(url)
        render_response(headers, body)

    elif args[0] == "-s":
        if len(args) < 2:
            print("Error: -s requires a search term.")
            print("Usage: go2web -s <search-term>")
            sys.exit(1)
        term = " ".join(args[1:])
        print(f'Searching for: "{term}"')
        interactive_search(term)

    else:
        print(f"Unknown option: {args[0]}")
        print(HELP_TEXT)
        sys.exit(1)


if __name__ == "__main__":
    main()