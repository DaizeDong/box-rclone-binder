#!/usr/bin/env python3
"""Entry point for the installed Linux runtime; no SSH recursion."""
from boxbinder.runtime import main

if __name__ == '__main__':
    raise SystemExit(main())
