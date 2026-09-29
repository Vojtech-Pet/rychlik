"""Device media capability profiles (Prompt A16 §15-18/§24).

A profile describes a combination of container + codecs known to be
broadly Device-Mode-share-compatible -- never a per-social-app whitelist
(§99-101).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DeviceMediaProfile:
    profile_id: str
    media_kind: str  # "video" | "audio"
    mime_type: str
    containers: frozenset[str]
    video_codecs: frozenset[str] = field(default_factory=frozenset)
    audio_codecs: frozenset[str] = field(default_factory=frozenset)
    pixel_formats: frozenset[str] = field(default_factory=frozenset)
    max_width: int | None = None
    max_height: int | None = None
    max_fps: float | None = None
    max_audio_channels: int | None = None


# §17: broad compatibility baseline -- not a guarantee every downstream
# app accepts the result (§99).
FRIENDSEND_GENERIC_VIDEO_V1 = DeviceMediaProfile(
    profile_id="friendsend-generic-video-v1",
    media_kind="video",
    mime_type="video/mp4",
    containers=frozenset({"mp4", "mov,mp4,m4a,3gp,3g2,mj2"}),
    video_codecs=frozenset({"h264"}),
    audio_codecs=frozenset({"aac"}),
    pixel_formats=frozenset({"yuv420p"}),
)

# §18
FRIENDSEND_GENERIC_AUDIO_V1 = DeviceMediaProfile(
    profile_id="friendsend-generic-audio-v1",
    media_kind="audio",
    mime_type="audio/mp4",
    containers=frozenset({"mp4", "mov,mp4,m4a,3gp,3g2,mj2", "ipod"}),
    audio_codecs=frozenset({"aac"}),
)

KNOWN_PROFILES: dict[str, DeviceMediaProfile] = {
    FRIENDSEND_GENERIC_VIDEO_V1.profile_id: FRIENDSEND_GENERIC_VIDEO_V1,
    FRIENDSEND_GENERIC_AUDIO_V1.profile_id: FRIENDSEND_GENERIC_AUDIO_V1,
}


@dataclass(frozen=True)
class DeviceMediaCapabilities:
    """What a specific, authenticated FriendSend device advertised
    (§20-22) -- never built from untrusted mDNS/display-name/IP data."""

    profiles: tuple[DeviceMediaProfile, ...] = ()

    @property
    def is_empty(self) -> bool:
        return len(self.profiles) == 0

    def profile_for(self, media_kind: str) -> DeviceMediaProfile | None:
        for profile in self.profiles:
            if profile.media_kind == media_kind:
                return profile
        return None

    @staticmethod
    def from_profile_ids(profile_ids: list[str]) -> "DeviceMediaCapabilities":
        """§24: unknown profile ids (an older peer, or a peer advertising
        a profile this desktop version doesn't recognize) are silently
        ignored, never guessed at -- this naturally falls back to "no
        known target profile" rather than a crash or an unsafe guess."""
        profiles = tuple(KNOWN_PROFILES[pid] for pid in profile_ids if pid in KNOWN_PROFILES)
        return DeviceMediaCapabilities(profiles=profiles)
