import pytest

from rychlik.share.public_url_builder import PublicUrlBuilder


def test_base_without_trailing_slash():
    urls = PublicUrlBuilder("http://127.0.0.1:8080")
    assert urls.media_url("abc") == "http://127.0.0.1:8080/media/abc"


def test_base_with_trailing_slash_normalizes():
    urls = PublicUrlBuilder("http://127.0.0.1:8080/")
    assert urls.media_url("abc") == "http://127.0.0.1:8080/media/abc"


def test_no_double_slash():
    urls = PublicUrlBuilder("http://127.0.0.1:8080/")
    for url in (urls.share_page_url("x"), urls.preview_url("x"), urls.media_url("x")):
        assert "//" not in url.split("://", 1)[1]


def test_all_three_routes():
    urls = PublicUrlBuilder("http://127.0.0.1:8080")
    assert urls.share_page_url("x") == "http://127.0.0.1:8080/s/x"
    assert urls.preview_url("x") == "http://127.0.0.1:8080/preview/x"
    assert urls.media_url("x") == "http://127.0.0.1:8080/media/x"


def test_https_style_future_tunnel_base():
    urls = PublicUrlBuilder("https://s.friendsend.example")
    assert urls.share_page_url("x") == "https://s.friendsend.example/s/x"


def test_empty_base_url_rejected():
    with pytest.raises(ValueError):
        PublicUrlBuilder("")


def test_ipv4_localhost_with_port():
    urls = PublicUrlBuilder("http://127.0.0.1:54321")
    assert urls.media_url("share1") == "http://127.0.0.1:54321/media/share1"
