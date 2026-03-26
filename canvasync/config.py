"""Configuration constants for Canvas sync."""

CONFIG_FILE = "config.ini"
DOWNLOAD_DIR = "temp_canvas_downloads"

# Performance tuning defaults (overridable via config.ini [PERFORMANCE])
DEFAULT_REQUEST_TIMEOUT = 20  # seconds
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_FACTOR = 0.5
DEFAULT_CANVAS_PER_PAGE = 100
DEFAULT_HTTP_POOL_MAXSIZE = 20
