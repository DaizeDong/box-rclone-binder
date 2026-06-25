"""box-binder — bind one Box drive across many servers via rclone, safely and idempotently.

Stdlib-only package. No third-party deps required (optional PyYAML used if present, else a
bundled minimal YAML subset parser). NEVER imports, logs, or returns secret values — only
*references* to where secrets live.
"""

__version__ = "0.1.0"

AUTH_MODES = ("jwt", "ccg-native", "ccg-mint", "oauth-broker")

# Exit codes — single source of truth (mirrors ARCHITECTURE §8).
EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_ALL_FAILED = 2
EXIT_CONFIG = 3
EXIT_UNREACHABLE = 4
EXIT_HEAL_FAILED = 5
