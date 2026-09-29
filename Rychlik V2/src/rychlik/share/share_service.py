"""Top-level ShareService: routes to DeviceShareService or ShareLinkService.

Each mode can be disabled independently without affecting the other (Prompt 03 gate).
"""

from __future__ import annotations

from rychlik.share.contracts import DeviceShareRequest, LinkShareRequest, ShareResult, ShareStatus
from rychlik.share.device_share_service import DeviceShareService
from rychlik.share.share_link_service import ShareLinkService


class ShareService:
    def __init__(
        self,
        *,
        device_enabled: bool = True,
        link_enabled: bool = True,
    ) -> None:
        self.device_enabled = device_enabled
        self.link_enabled = link_enabled
        self._device_service = DeviceShareService() if device_enabled else None
        self._link_service = ShareLinkService() if link_enabled else None

    def send_to_device(self, request: DeviceShareRequest) -> ShareResult:
        if self._device_service is None:
            return ShareResult(status=ShareStatus.FAILED, share_id="", error="device mode disabled")
        return self._device_service.start_share(request)

    def share_by_link(self, request: LinkShareRequest) -> ShareResult:
        if self._link_service is None:
            return ShareResult(status=ShareStatus.FAILED, share_id="", error="link mode disabled")
        return self._link_service.create_link(request)
