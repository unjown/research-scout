from cloud.collectors.news import allow_news


def test_cloud_news_filter_has_no_topic_blacklist() -> None:
    assert allow_news("Vitamin C study", "Biomedical discovery", "technology")
    assert allow_news("Celebrity entertainment announcement", "Casting news", "philippines")


def test_cloud_news_filter_still_rejects_malformed_title() -> None:
    assert not allow_news("", "Summary", "technology")
    assert not allow_news("   ", "Summary", "technology")
