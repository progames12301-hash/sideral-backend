"""Mount the isolated stdlib API into the existing Sideral HTTP server."""
import os
import threading
from .server import Store, handler_for

_handler=None
_lock=threading.Lock()


def _radar_origins():
    """Return the Sideral frontend origins allowed to consume radar data."""
    configured = os.environ.get('RADAR_V3_ORIGINS')
    if configured:
        return configured
    return ','.join((
        'https://sideralmeteorologiabrasil.web.app',
        'https://sideral-meteorologia.pages.dev',
    ))


def dispatch(request):
    global _handler
    with _lock:
        if _handler is None:
            # Keep the radar API usable by both the existing Firebase frontend
            # and the current Cloudflare Pages deployment. A deployment can
            # override this list with RADAR_V3_ORIGINS without changing code.
            os.environ.setdefault('RADAR_V3_ORIGINS', _radar_origins())
            store=Store(os.environ.get('RADAR_V3_INPUT','radar_v3_data'),os.environ.get('RADAR_V3_CACHE','radar_v3_cache'),cptec=True)
            _handler=handler_for(store)
            feeds=os.environ.get('RADAR_V3_FEEDS')
            if feeds:
                from .ingest import poll_feeds
                threading.Thread(target=poll_feeds,args=(store.root,feeds),daemon=True).start()
    # Reuse parsed request/streams; do NOT construct a second socket handler or
    # close the original connection. V3 headers remain independent of legacy CORS.
    mounted=object.__new__(_handler)
    mounted.__dict__.update(request.__dict__)
    if request.command=='OPTIONS': mounted.do_OPTIONS()
    else: mounted.do_GET()
