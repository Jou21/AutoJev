"""One offline export per service, with snapshot/code/evidence freshness checks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from shutil import copyfile
from uuid import uuid4


class ReportCache:
    def __init__(self):
        self.key = None
        self.path = None
        self.dependencies = {}
        self.identity = uuid4().hex

    @staticmethod
    def signature(path):
        try:
            stat = path.stat()
            return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
        except FileNotFoundError:
            return None

    def render(self, renderer, snapshot, destination, artifact_dir, progress=None):
        # Only the request-time display stamp is volatile. Scope, case selection,
        # recipes and every other rendered field remain part of the key.
        stable = deepcopy(snapshot)
        if "suite_context" in stable:
            stable["suite_context"].pop("generated_at", None)
        source_root = Path(__file__).resolve().parents[1]
        sources = sorted(path for path in source_root.rglob("*") if path.suffix in {".py", ".css", ".js"})
        code = [(str(path), self.signature(path)) for path in sources]
        key = hashlib.sha256(json.dumps([stable, code], sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        if (key == self.key and self.path and self.path.is_file()
                and all(self.signature(path) == signature for path, signature in self.dependencies.items())):
            if progress:
                progress.update("cache")
            copyfile(self.path, destination)
            if progress:
                progress.update("ready", 1, 1)
            return
        renderer.generate(snapshot, str(destination))
        if progress:
            progress.check()
        # Captured before evidence reads, including missing files. Changes during
        # rendering invalidate the next request rather than blessing stale bytes.
        dependencies = renderer.capture_dependencies.copy()
        cache = artifact_dir / f".suite-report-cache-{self.identity}.html"
        # No responses use this file directly; the service lock protects copies.
        # Invalidate first so a failed copy can never publish a partial cache.
        self.key = None
        copyfile(destination, cache)
        if progress:
            progress.check()
        self.path, self.dependencies, self.key = cache, dependencies, key
        if progress:
            progress.update("ready", 1, 1)
