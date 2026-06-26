"""prismatic.social — auto-generate social posts from media library.

GRO-572: Pipeline that selects 3-5 photos/day from a media library,
generates captions from tags/metadata, queues them, and posts via the
Meta Graph API (Instagram/Facebook).

Design notes
------------
- **Lane**: ``prismatic/`` (write-access for Ned).
- **Dry-run by default**: any CLI invocation without ``META_ACCESS_TOKEN``
  runs the full pipeline through the queue-store and writes no traffic to
  Meta. This is what cron uses; real posting is gated behind live creds.
- **State machine** for posts: ``PENDING -> SCHEDULED -> POSTING -> POSTED``
  with ``FAILED`` as a sink and ``RETRY`` as a recovery transition.
- **JSON queue** at ``$PRISMATIC_HOME/social_queue.json`` keeps the surface
  auditable. No DB, no migration overhead — matches the rest of the engine.
"""

from prismatic.social.config import SocialConfig, load_config
from prismatic.social.models import Caption, Photo, Post, PostStatus
from prismatic.social.selector import PhotoSelector
from prismatic.social.captioner import CaptionGenerator
from prismatic.social.meta_client import MetaGraphClient, DryRunMetaClient
from prismatic.social.queue_store import QueueStore
from prismatic.social.pipeline import SocialPipeline

__all__ = [
    "Caption",
    "CaptionGenerator",
    "DryRunMetaClient",
    "MetaGraphClient",
    "Photo",
    "PhotoSelector",
    "Post",
    "PostStatus",
    "QueueStore",
    "SocialConfig",
    "SocialPipeline",
    "load_config",
]
