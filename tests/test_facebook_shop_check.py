from app.services.facebook_client import FacebookAPIError
from app.services.facebook_shop_check import classify_page, looks_like_shopee


def test_looks_like_shopee_urls():
    assert looks_like_shopee("Mua ngay https://shopee.vn/product/1")
    assert looks_like_shopee("https://shp.ee/abc")
    assert not looks_like_shopee("Video mới #reviewphim")


class FakeClient:
    def __init__(self, posts=None, profile=None, fail=False):
        self.posts = posts or []
        self.profile = profile or {"name": "Page A", "followers_count": 1200}
        self.fail = fail

    def get_page_profile(self, page_id, token):
        if self.fail:
            raise FacebookAPIError("no permission")
        return self.profile

    def list_recent_posts(self, page_id, token, limit=12):
        return self.posts


def test_classify_page_finds_shopee_link():
    client = FakeClient(posts=[{"id": "1", "message": "Xem clip https://shopee.vn/abc"}])
    row = classify_page(
        {"page_id": "p1", "name": "Page A", "can_publish": True, "followers_count": 10},
        client,
        "tok",
    )
    assert row["status"] == "has_cart_signal"
    assert row["shopee_posts"] == 1


def test_classify_page_no_token():
    row = classify_page({"page_id": "p1", "name": "Page A", "can_publish": True}, FakeClient(), "")
    assert row["status"] == "no_token"


def test_classify_page_no_signal():
    client = FakeClient(posts=[{"id": "1", "message": "Review phim cổ trang"}])
    row = classify_page(
        {"page_id": "p1", "name": "Page A", "can_publish": True, "followers_count": 500},
        client,
        "tok",
    )
    assert row["status"] == "no_signal"
