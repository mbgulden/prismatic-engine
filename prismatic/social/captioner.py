"""CaptionGenerator — turn a Photo into a Caption.

The generator is deliberately deterministic and dependency-free. v1 does
not call an LLM; it composes a caption from three layers:

1. A *hook* sentence picked from a small bank, biased by the photo's
   tags (e.g. a tag of ``sunset`` selects the "Golden hour" hook).
2. The photo's sidecar ``caption_seed`` (if any) — long-form copy.
3. Hashtags: the photo's tags become ``#tag`` items first, then
   ``SocialConfig.hashtags`` pads to a minimum of 5 (Instagram best
   practice is 8-15; we cap at 15 to avoid spam heuristics).

The LLM-generated copy path is intentionally pluggable: the class
exposes ``generate(photo)`` and a subclass can override it without
breaking the pipeline contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from prismatic.social.config import SocialConfig
from prismatic.social.exceptions import CaptionError
from prismatic.social.models import Caption, Photo


# Hook bank keyed by tag-substring -> list of candidate hooks.
_HOOK_BANK: dict[str, list[str]] = {
    "sunset": ["Golden hour, captured.", "Sunset state of mind.", "Chasing the light."],
    "sunrise": ["First light of the day.", "An early start."],
    "ocean": ["Pacific blues.", "Where the water meets the sky.", "Salt in the air."],
    "beach": ["Beach days are the best days.", "Sand between the pixels."],
    "hike": ["Earned views.", "Trail tested.", "Up, up, up."],
    "mountain": ["Big sky country.", "Altitude with attitude."],
    "city": ["City lights.", "Urban canvas."],
    "food": ["Taste the moment.", "Plated up."],
    "portrait": ["Meet the maker.", "Faces of the work."],
    "team": ["The crew.", "Better together."],
    "office": ["Where the work happens.", "Day one energy."],
    "event": ["Live and in person.", "Moments from the floor."],
    "product": ["Fresh out of the studio.", "Built for this."],
}

_FALLBACK_HOOKS = [
    "From the studio today.",
    "Behind the scenes.",
    "A little something for the feed.",
]

_MAX_HASHTAGS = 15
_MIN_HASHTAGS = 5


@dataclass
class CaptionGenerator:
    config: SocialConfig

    def generate(self, photo: Photo) -> Caption:
        if not photo.path:
            raise CaptionError("photo.path is empty")
        hook = self._pick_hook(photo)
        body = self._compose_body(hook=hook, photo=photo)
        tags = self._build_hashtags(photo)
        if not body.strip():
            raise CaptionError(f"empty caption produced for {photo.path}")
        return Caption(photo_id=photo.photo_id, text=body, hashtags=tags)

    # -- internals ---------------------------------------------------------

    def _pick_hook(self, photo: Photo) -> str:
        lowered = [t.lower() for t in photo.tags]
        for tag in lowered:
            for needle, hooks in _HOOK_BANK.items():
                if needle in tag and hooks:
                    idx = sum(ord(c) for c in photo.photo_id) % len(hooks)
                    return hooks[idx]
        idx = sum(ord(c) for c in photo.photo_id) % len(_FALLBACK_HOOKS)
        return _FALLBACK_HOOKS[idx]

    def _compose_body(self, *, hook: str, photo: Photo) -> str:
        parts: list[str] = [hook]
        if photo.caption_seed:
            seed = photo.caption_seed.strip()
            if seed:
                parts.append(seed)
        if photo.tags:
            tag_line = " · ".join(photo.tags[:5])
            parts.append(tag_line)
        return "\n\n".join(parts)

    def _build_hashtags(self, photo: Photo) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        # Photo tags first (highest signal).
        for t in photo.tags:
            h = self._normalize_hashtag(t)
            if h and h not in seen:
                seen.add(h)
                out.append(h)
            if len(out) >= _MAX_HASHTAGS:
                break
        # Then config fallback hashtags.
        if len(out) < _MIN_HASHTAGS:
            for t in self.config.hashtags:
                h = self._normalize_hashtag(t)
                if h and h not in seen:
                    seen.add(h)
                    out.append(h)
                if len(out) >= _MIN_HASHTAGS:
                    break
        # Pad with stable derived tags so a hashtag-less photo still gets 5.
        i = 0
        while len(out) < _MIN_HASHTAGS:
            tag = f"#photo{i:02d}"
            if tag not in seen:
                seen.add(tag)
                out.append(tag)
            i += 1
            if i > 99:  # hard safety
                break
        return out[:_MAX_HASHTAGS]

    @staticmethod
    def _normalize_hashtag(raw: str) -> str:
        s = raw.strip()
        if not s:
            return ""
        if not s.startswith("#"):
            s = "#" + s
        # Instagram hashtag rules: letters, numbers, underscore.  Strip everything else.
        s = re.sub(r"[^A-Za-z0-9_#]", "", s)
        s = re.sub(r"^#+(?=[A-Za-z0-9])", "#", s)
        if len(s) < 2:
            return ""
        return s
