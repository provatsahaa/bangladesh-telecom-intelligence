"""Allow running via `python -m bd_telecom_scraper`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
