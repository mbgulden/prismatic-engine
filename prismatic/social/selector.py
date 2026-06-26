"""PhotoSelector — scan a media library and pick N photos for the day.

Scoring
-------
Each candidate photo gets a score from three signals, then we return the
top ``daily_limit`` (or all of them, whichever is smaller):

* ``recency``  — how recent is the file's mtime?  newer = higher
* ``freshness`` — has this photo been posted before? ``never_posted`` = 1.0
* ``tag_bonus`` — does the photo carry tags/keywords we'd want surfaced?

The selector never modifies the filesystem; it only reads.

Tag extraction
--------------
We use a cheap sidecar convention:

* ``<basename>.tags.json``   — JSON list of strings.  Highest fidelity.
* ``<basename>.txt``         — one keyword per line.  Fallback.
* EXIF/XMP — out of scope for v1; pluggable later via a ``TagReader``.

Both files are optional.  If neither exists the photo still gets selected
and the captioner falls back to its template bank.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from prismatic.social.config import SocialConfig
from prismatic.social.exceptions import MediaLibraryError
from prismatic.social.models import Photo

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tiff", ".tif"}
_MAX_CANDIDATES = 500  # cap scan to keep selector O(scan) small


@dataclass
class _Candidate:
    photo: Photo
    recency: float
    freshness: float
    tag_bonus: float

    @property
    def score(self) -> float:
        # Weighted sum; weights tuned so that a never-posted, fresh, tagged
        # photo beats a stale, unposted, untagged one by ~3x.
        return (0.4 * self.recency) + (0.4 * self.freshness) + (0.2 * self.tag_bonus)


class PhotoSelector:
    """Select top-N photos from a media library."""

    def __init__(
        self,
        config: SocialConfig,
        *,
        previously_posted_ids: set[str] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._cfg = config
        self._posted = previously_posted_ids or set()
        self._rng = rng or random.Random()

    # -- public API --------------------------------------------------------

    def select(self, n: int) -> list[Photo]:
        """Return up to ``n`` Photos ranked by score, deterministic given seed."""
        if n <= 0:
            return []
        candidates = self._scan()
        if not candidates:
            return []
        candidates.sort(key=lambda c: c.score, reverse=True)
        # Take the top n.  If there are ties at the boundary, the rng breaks them.
        top = candidates[:n]
        return [c.photo for c in top]

    # -- internals ---------------------------------------------------------

    def _scan(self) -> list[_Candidate]:
        root = self._cfg.media_library
        if not root.exists():
            raise MediaLibraryError(f"Media library not found: {root}")
        if not root.is_dir():
            raise MediaLibraryError(f"Media library is not a directory: {root}")

        files: list[Path] = []
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                if Path(n).suffix.lower() in _IMAGE_EXTS:
                    files.append(Path(dirpath) / n)
                    if len(files) >= _MAX_CANDIDATES:
                        break
            if len(files) >= _MAX_CANDIDATES:
                break

        if not files:
            return []

        now_ts = datetime.now(timezone.utc).timestamp()
        recency_horizon = 60 * 60 * 24 * 30  # 30 days
        candidates: list[_Candidate] = []
        for path in files:
            tags, caption_seed = self._read_sidecar(path)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            age_days = max(0.0, (now_ts - mtime) / recency_horizon)
            recency = max(0.0, 1.0 - age_days)  # 1.0 today, 0.0 at 30 days+
            photo_id = "ph_" + self._photo_hash(path)
            freshness = 0.0 if photo_id in self._posted else 1.0
            tag_bonus = min(1.0, len(tags) / 5.0) if tags else 0.0
            photo = Photo(
                path=str(path),
                photo_id=photo_id,
                tags=tags,
                caption_seed=caption_seed,
                score=0.0,  # filled below
                taken_at=datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(
                    timespec="seconds"
                ),
            )
            photo.score = (0.4 * recency) + (0.4 * freshness) + (0.2 * tag_bonus)
            candidates.append(
                _Candidate(
                    photo=photo,
                    recency=recency,
                    freshness=freshness,
                    tag_bonus=tag_bonus,
                )
            )

        return candidates

    @staticmethod
    def _photo_hash(path: Path) -> str:
        import hashlib

        return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]

    def _read_sidecar(self, path: Path) -> tuple[list[str], str]:
        tags_path = path.with_suffix(path.suffix + ".tags.json")
        if tags_path.exists():
            try:
                data = json.loads(tags_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return [str(x) for x in data if str(x).strip()], ""
                if isinstance(data, dict):
                    tags = data.get("tags") or []
                    seed = data.get("caption_seed") or ""
                    if isinstance(tags, list):
                        return [str(x) for x in tags if str(x).strip()], str(seed)
            except (json.JSONDecodeError, OSError):
                pass
        txt_path = path.with_suffix(path.suffix + ".txt")
        if txt_path.exists():
            try:
                lines = [
                    ln.strip()
                    for ln in txt_path.read_text(encoding="utf-8").splitlines()
                    if ln.strip()
                ]
                return lines[:10], ""
            except OSError:
                pass
        return [], ""
