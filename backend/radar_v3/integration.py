"""Mount the isolated stdlib API into the existing Sideral HTTP server."""
import logging
import os
import subprocess
import sys
import threading
import time

from .server import Store, handler_for

_handler = None
_lock = threading.Lock()
_raw_thread = None
LOG = logging.getLogger('BRASIL-SCOPE-V3')


def _radar_origins():
    """Return the Sideral frontend origins allowed to consume radar data."""
    configured = os.environ.get('RADAR_V3_ORIGINS')
    if configured:
        return configured
    return ','.join((
        'https://sideralmeteorologiabrasil.web.app',
        'https://sideral-meteorologia.pages.dev',
        'https://sideralmeteorologia.progames12301.workers.dev',
    ))


def _cptec_raw_worker(root):
    """Materialize CPTEC polar volumes in an isolated child process."""
    interval = max(120, int(os.environ.get('RADAR_V3_CPTEC_RAW_INTERVAL', '600')))
    limit = max(1, min(96, int(os.environ.get('RADAR_V3_CPTEC_RAW_FILES', '24'))))
    timeout = max(60, int(os.environ.get('RADAR_V3_CPTEC_RAW_TIMEOUT', '540')))
    while True:
        try:
            completed = subprocess.run(
                [sys.executable, '-m', 'backend.radar_v3.cptec_raw_worker', str(root), str(limit)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors='replace',
                timeout=timeout,
                check=False,
            )
            if completed.returncode != 0:
                LOG.warning('[CPTEC-RAW] processo filho terminou com código %s%s', completed.returncode,
                            (' · ' + completed.stdout[-1500:].strip()) if completed.stdout else '')
        except subprocess.TimeoutExpired:
            LOG.warning('[CPTEC-RAW] processo filho excedeu %ss e foi encerrado', timeout)
        except Exception as exc:
            LOG.warning('[CPTEC-RAW] falha ao iniciar/conduzir worker: %s', exc)
        time.sleep(interval)


def _start_cptec_raw_worker(root):
    global _raw_thread
    # Disabled by default because the public CPTEC volumetric directory currently
    # contains historical files rather than a live stream. Enable explicitly with
    # RADAR_V3_CPTEC_RAW=1 when that upstream volume feed is live again.
    if str(os.environ.get('RADAR_V3_CPTEC_RAW', '0')).lower() in ('0', 'false', 'no', 'off'):
        return
    if _raw_thread and _raw_thread.is_alive():
        return
    _raw_thread = threading.Thread(target=_cptec_raw_worker, args=(root,), name='cptec-raw-radar', daemon=True)
    _raw_thread.start()


def dispatch(request):
    global _handler
    with _lock:
        if _handler is None:
            os.environ.setdefault('RADAR_V3_ORIGINS', _radar_origins())
            root = os.environ.get('RADAR_V3_INPUT', 'radar_v3_data')
            store = Store(root, os.environ.get('RADAR_V3_CACHE', 'radar_v3_cache'), cptec=True)
            _handler = handler_for(store)
            _start_cptec_raw_worker(root)
            feeds = os.environ.get('RADAR_V3_FEEDS')
            if feeds:
                from .ingest import poll_feeds
                threading.Thread(target=poll_feeds, args=(store.root, feeds), daemon=True).start()

    mounted = object.__new__(_handler)
    mounted.__dict__.update(request.__dict__)
    if request.command == 'OPTIONS':
        mounted.do_OPTIONS()
    else:
        mounted.do_GET()
