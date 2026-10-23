import sys

from config_ops import main

if __name__ == "__main__":
    # Banner and emojis need UTF-8, also when output is redirected on Windows
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]
    main()
