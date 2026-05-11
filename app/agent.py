"""Privileged nginx configuration agent.

The full socket protocol is implemented in a later plan step. This skeleton
keeps the supervised process explicit while the project foundation is built.
"""

import signal
import time


def main() -> None:
    running = True

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while running:
        time.sleep(1)


if __name__ == "__main__":
    main()
