from app.core.database import get_db_connection, init_db
from app.models.job import ReupConfig
from app.services.affiliate_link import (
    affiliate_from_mapping,
    caption_lead,
    comment_body,
    load_affiliate_from_job,
    looks_like_single_product,
    normalize_affiliate_url,
    persist_affiliate_on_job,
    prepend_affiliate_caption,
    product_label,
)
from app.services.facebook_distribution import _caption


def test_normalize_affiliate_url_extracts_shopee():
    assert normalize_affiliate_url(
        "mua ngay https://shopee.vn/product/123?utm=1."
    ) == "https://shopee.vn/product/123?utm=1"
    assert normalize_affiliate_url("shp.ee/abcxyz") == "https://shp.ee/abcxyz"
    assert normalize_affiliate_url("javascript:alert(1)") == ""
    assert normalize_affiliate_url("") == ""


def test_cta_copy_matches_requested_shape():
    url = "https://shopee.vn/giay-ve-sinh-i.1.2"
    lead = caption_lead(url, "giấy vệ sinh")
    assert lead.startswith("https://shopee.vn/giay-ve-sinh-i.1.2")
    assert "Bạn cần mua giấy vệ sinh hãy ủng hộ shop qua link: https://shopee.vn/giay-ve-sinh-i.1.2" in lead
    comment = comment_body(url, "giấy vệ sinh")
    assert comment.startswith(url)
    assert "ủng hộ kênh qua: https://shopee.vn/giay-ve-sinh-i.1.2" in comment
    assert product_label("") == "sản phẩm"


def test_looks_like_single_product():
    assert looks_like_single_product("https://shopee.vn/giay-ve-sinh-i.123.456")
    assert looks_like_single_product("https://shp.ee/abcxyz")
    assert looks_like_single_product("https://shopee.vn/product/123/456")
    assert not looks_like_single_product("https://shopee.vn/shop/123")
    assert not looks_like_single_product("https://shopee.vn/mall/foo")
    assert not looks_like_single_product("")


def test_prepend_affiliate_caption_is_idempotent():
    url = "https://shp.ee/abc"
    original = "Phế phi bị đày vào lãnh cung\n\n#reviewphim"
    once = prepend_affiliate_caption(original, url, "giấy vệ sinh")
    twice = prepend_affiliate_caption(once, url, "giấy vệ sinh")
    assert once.startswith("https://shp.ee/abc")
    assert "Bạn cần mua giấy vệ sinh hãy ủng hộ shop qua link: https://shp.ee/abc" in once
    assert once.endswith(original)
    assert twice == once
    assert prepend_affiliate_caption("hello", "") == "hello"


def test_reup_config_keeps_affiliate_fields():
    cfg = ReupConfig(affiliate_link=" https://shopee.vn/x ", affiliate_product=" giấy vệ sinh ")
    assert cfg.affiliate_link == "https://shopee.vn/x"
    assert cfg.affiliate_product == "giấy vệ sinh"


def test_facebook_caption_helper_puts_link_first():
    row = {"caption": "Tóm tắt clip", "tags": '["reviewphim"]'}
    text = _caption(row, affiliate_url="https://shp.ee/zz", affiliate_product="giấy vệ sinh")
    assert text.startswith("https://shp.ee/zz")
    assert "Bạn cần mua giấy vệ sinh hãy ủng hộ shop qua link: https://shp.ee/zz" in text
    assert "Tóm tắt clip" in text
    assert "#reviewphim" in text


def test_persist_and_load_affiliate_on_job(tmp_path):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    with get_db_connection(db) as conn:
        conn.execute(
            """INSERT INTO jobs (job_id, source_url, platform, status, progress_percent,
                output_file_path, watermark_config, reup_config, created_at, updated_at, message, logs)
               VALUES ('job_a', '', 'youtube', 'COMPLETED', 100, '', '{}', '{}', '', '', '', '[]')"""
        )
        conn.commit()
    persist_affiliate_on_job(
        db, "job_a", url="https://shopee.vn/abc", product="giấy vệ sinh"
    )
    url, product = load_affiliate_from_job(db, "job_a")
    assert url == "https://shopee.vn/abc"
    assert product == "giấy vệ sinh"
    assert affiliate_from_mapping({"affiliate_link": url, "affiliate_product": product}) == (url, product)
