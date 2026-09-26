"""Required contract addition for Prompt 07 (see docs/LOCAL_SHARE_ORIGIN_RESULT.md).

Before this phase, no Artifact was ever persisted anywhere: it was created
ad hoc and handed directly into a ShareRequest for the duration of one call.
LocalShareOrigin needs to resolve artifact_id -> Artifact for requests that
arrive later, out of band from creation, so a minimal repository is needed.
Mirrors ShareLinkRepository's shape deliberately (same seam, same size).
"""

from __future__ import annotations

from typing import Protocol

from rychlik.core.artifact import Artifact


class ArtifactRepository(Protocol):
    def save(self, artifact: Artifact) -> None: ...

    def get(self, artifact_id: str) -> Artifact | None: ...


class InMemoryArtifactRepository:
    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}

    def save(self, artifact: Artifact) -> None:
        self._artifacts[artifact.artifact_id] = artifact

    def get(self, artifact_id: str) -> Artifact | None:
        return self._artifacts.get(artifact_id)
