from __future__ import annotations

from urllib.parse import urlparse

import server_legacy as legacy
import stations_sideral


class Handler(legacy.Handler):
    """Adds the bulk Sideral stations feed without touching legacy routes."""

    def do_GET(self) -> None:
        parsed_path = urlparse(self.path).path
        if parsed_path in {"/api/estacoes/sideral", "/api/stations/sideral"}:
            try:
                stations_sideral.handle(self)
            except Exception as exc:
                self.send_json(
                    502,
                    {
                        "status": False,
                        "error": "Estações Sideral temporariamente indisponíveis.",
                        "details": f"{type(exc).__name__}: {exc}",
                    },
                )
            return
        super().do_GET()


def main() -> None:
    # legacy.main creates the ThreadingHTTPServer from the module-global Handler.
    # Replacing it here preserves every existing route while adding ours.
    legacy.Handler = Handler
    legacy.main()


if __name__ == "__main__":
    main()
