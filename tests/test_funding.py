from eu_startups_pipeline.funding import funding_bucket_allowed, normalize_funding_bucket


def test_normalize_known_bucket():
    info = normalize_funding_bucket(
        "€1-5 million",
        {"€1-5 million": {"min": 1_000_000, "max": 5_000_000}},
    )
    assert info.key == "€1-5 million"
    assert info.min_eur == 1_000_000
    assert info.max_eur == 5_000_000
    assert info.is_known_bucket is True


def test_normalize_unknown_bucket_range():
    info = normalize_funding_bucket("€5-12 million", {})
    assert info.min_eur == 5_000_000
    assert info.max_eur == 12_000_000
    assert info.is_known_bucket is False


def test_funding_bucket_policy():
    policy = {"allowed_buckets": ["€1-5 million"], "excluded_buckets": ["No funding announced yet"]}
    assert funding_bucket_allowed("€1-5 million", policy) is True
    assert funding_bucket_allowed("No funding announced yet", policy) is False
