"""Server-rendered HTML for GET /s/<share_id> (Prompt 09).

All Open Graph metadata is present in the initial HTML response — no
JavaScript, no client-side rendering, so preview crawlers that never
execute JS still see correct title/description/image/url. This module is
pure (string in, string out); it never touches the filesystem or network.

Chosen policy (documented, not accidental):
- Preview image, if present, is used for og:image and (for ACTIVE video
  shares) as the <video poster>. If SharePreview has no thumbnail
  (thumbnail_path is None), og:image is simply omitted (§11 option B).
- All dynamic text is HTML-escaped via the stdlib `html` module — never a
  manual "<"/">" replace.
- noindex/nofollow is advisory only (§37) — it is not an access-control
  mechanism; the real protection is the unguessable share_id plus
  ShareLink's revoke/expiry lifecycle.
"""

from __future__ import annotations

import html as html_lib

from rychlik.share.contracts import ShareStatus
from rychlik.share.public_url_builder import PublicUrlBuilder
from rychlik.share.share_preview import MediaKind, SharePreview

_STATE_MESSAGES: dict[ShareStatus, str] = {
    ShareStatus.CREATING: "Preparing your share...",
    ShareStatus.OFFLINE: "This video is currently unavailable because the sender is offline.",
    ShareStatus.EXPIRED: "This share has expired.",
    ShareStatus.REVOKED: "This share has been revoked.",
    ShareStatus.FAILED: "This share is unavailable.",
}


def render_share_page(
    *,
    status: ShareStatus,
    share_id: str,
    preview: SharePreview | None,
    urls: PublicUrlBuilder,
    media_mime_type: str | None,
) -> str:
    title = html_lib.escape(preview.title) if preview is not None else "Shared file"
    description = html_lib.escape(preview.description) if preview is not None else ""
    page_url = html_lib.escape(urls.share_page_url(share_id))

    has_image = preview is not None and preview.thumbnail_path is not None
    image_url = html_lib.escape(urls.preview_url(share_id)) if has_image else None

    og_tags = [
        f'<meta property="og:title" content="{title}">',
        f'<meta property="og:description" content="{description}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:url" content="{page_url}">',
    ]
    if image_url is not None:
        og_tags.append(f'<meta property="og:image" content="{image_url}">')
        if preview.width and preview.height:
            og_tags.append(f'<meta property="og:image:width" content="{preview.width}">')
            og_tags.append(f'<meta property="og:image:height" content="{preview.height}">')
        og_tags.append('<meta property="og:image:type" content="image/jpeg">')

    body_blocks: list[str] = [f"<h1>{title}</h1>"]
    if description:
        body_blocks.append(f"<p>{description}</p>")

    if status is ShareStatus.ACTIVE:
        media_url = html_lib.escape(urls.media_url(share_id))
        if preview is not None and preview.media_kind is MediaKind.VIDEO:
            poster_attr = f' poster="{image_url}"' if image_url else ""
            mime = html_lib.escape(media_mime_type or "video/mp4")
            body_blocks.append(
                f'<video controls preload="metadata"{poster_attr}>'
                f'<source src="{media_url}" type="{mime}"></video>'
            )
        else:
            body_blocks.append(f'<a href="{media_url}">Download</a>')
    else:
        message = html_lib.escape(_STATE_MESSAGES.get(status, "This share is unavailable."))
        body_blocks.append(f"<p>{message}</p>")
        if image_url is not None:
            body_blocks.append(f'<img src="{image_url}" alt="">')

    og_html = "\n".join(og_tags)
    body_html = "\n".join(body_blocks)

    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex,nofollow">\n'
        f'<meta name="description" content="{description}">\n'
        f"{og_html}\n"
        f"<title>{title}</title>\n"
        "</head>\n"
        "<body>\n"
        f"{body_html}\n"
        "</body>\n"
        "</html>\n"
    )


def render_not_found_page() -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="robots" content="noindex,nofollow">\n'
        "<title>Not found</title>\n"
        "</head>\n"
        "<body>\n"
        "<p>This share does not exist or is no longer available.</p>\n"
        "</body>\n"
        "</html>\n"
    )
