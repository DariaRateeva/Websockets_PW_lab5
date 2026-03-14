import sys

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
        print(f"TODO: fetch {url}")

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