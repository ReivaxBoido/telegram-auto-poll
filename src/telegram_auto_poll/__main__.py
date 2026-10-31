"""Module entry point: ``python -m telegram_auto_poll``.

Only the bootstrap lives here; :func:`telegram_auto_poll.app.main` owns the lifecycle. The file is
excluded from coverage under the constitution §11.3 ``__main__`` exception.
"""

from __future__ import annotations

from .app import main

if __name__ == "__main__":
    raise SystemExit(main())
