"""Tiny in-memory job runner for long operations (PoE cycle, firmware push, discovery...)."""
import itertools
import threading
import time
import traceback

_jobs = {}
_ids = itertools.count(1)
_lock = threading.Lock()


def submit(kind, target, fn, *args, **kwargs):
    jid = next(_ids)
    job = {"id": jid, "kind": kind, "target": target, "state": "running", "started": time.time(),
           "finished": None, "result": None, "error": None, "progress": ""}
    with _lock:
        _jobs[jid] = job

    def run():
        try:
            job["result"] = fn(job, *args, **kwargs)
            job["state"] = "done"
        except Exception as e:  # noqa: BLE001 - jobs report any failure to the UI
            job["state"] = "failed"
            job["error"] = str(e) or e.__class__.__name__
            traceback.print_exc()
        job["finished"] = time.time()

    threading.Thread(target=run, daemon=True, name=f"job-{jid}").start()
    _gc()
    return job


def get(jid):
    return _jobs.get(jid)


def recent(limit=50):
    with _lock:
        return sorted(_jobs.values(), key=lambda j: -j["id"])[:limit]


def _gc():
    cutoff = time.time() - 6 * 3600
    with _lock:
        for k in [k for k, j in _jobs.items() if j["finished"] and j["finished"] < cutoff]:
            del _jobs[k]
