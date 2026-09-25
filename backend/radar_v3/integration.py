"""Mount the isolated stdlib API into the existing Sideral HTTP server."""
import os
import threading
import time
from .server import Store, handler_for

_handler=None
_lock=threading.Lock()
_raw_thread=None


def _radar_origins():
    """Return the Sideral frontend origins allowed to consume radar data."""
    configured = os.environ.get('RADAR_V3_ORIGINS')
    if configured:
        return configured
    return ','.join((
        'https://sideralmeteorologiabrasil.web.app',
        'https://sideral-meteorologia.pages.dev',
    ))


def _cptec_raw_worker(root):
    """Keep the public CPTEC volumetric polar feed materialized for V5.

    The worker is intentionally conservative: the adapter only converts files
    that expose native elevation + azimuth + range coordinates. Invalid or
    Cartesian-only NetCDF files are left untouched and never become fake gates.
    """
    interval=max(120,int(os.environ.get('RADAR_V3_CPTEC_RAW_INTERVAL','600')))
    limit=max(1,min(96,int(os.environ.get('RADAR_V3_CPTEC_RAW_FILES','24'))))
    while True:
        try:
            from .adapters.cptec_raw import ensure_recent
            ensure_recent(root,limit)
        except Exception:
            # A temporary CPTEC/FTP failure must not take the Sideral HTTP server down.
            pass
        time.sleep(interval)


def _start_cptec_raw_worker(root):
    global _raw_thread
    if str(os.environ.get('RADAR_V3_CPTEC_RAW','1')).lower() in ('0','false','no','off'):
        return
    if _raw_thread and _raw_thread.is_alive():
        return
    _raw_thread=threading.Thread(target=_cptec_raw_worker,args=(root,),name='cptec-raw-radar',daemon=True)
    _raw_thread.start()


def dispatch(request):
    global _handler
    with _lock:
        if _handler is None:
            # Keep the radar API usable by both the existing Firebase frontend
            # and the current Cloudflare Pages deployment. A deployment can
            # override this list with RADAR_V3_ORIGINS without changing code.
            os.environ.setdefault('RADAR_V3_ORIGINS', _radar_origins())
            root=os.environ.get('RADAR_V3_INPUT','radar_v3_data')
            store=Store(root,os.environ.get('RADAR_V3_CACHE','radar_v3_cache'),cptec=True)
            _handler=handler_for(store)
            _start_cptec_raw_worker(root)
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
