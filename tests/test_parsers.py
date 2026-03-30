from eu_startups_pipeline.parsers import parse_company_page, parse_search_results

SEARCH_HTML = """
<html>
  <body>
    <h3>Search Results (1869)</h3>
    <div class="wpbdp-listing-excerpt">
      <h3><a href="/directory/acme-ai/">Acme AI</a></h3>
      <div>Category: Germany</div>
      <div>Based in: Berlin</div>
      <div>Tags: AI, SaaS</div>
      <div>Founded: 2024</div>
    </div>
    <a class="next-page" href="/directory/page/2/?foo=bar">Next</a>
  </body>
</html>
"""


COMPANY_HTML = """
<html>
  <body>
    <h1 class="entry-title">Acme AI</h1>
    <div class="wpbdp-field-display">
      <span class="field-label">Based in:</span> Berlin
    </div>
    <div class="wpbdp-field-display">
      <span class="field-label">Website:</span> https://acme.example
    </div>
    <div class="wpbdp-field-display">
      <span class="field-label">Total Funding:</span> €1-5 million
    </div>
    <div class="wpbdp-field-display">
      <span class="field-label">Long Business Description:</span> Applied AI for logistics.
    </div>
    <a href="https://www.linkedin.com/company/acme-ai/about/">linkedin</a>
  </body>
</html>
"""


def test_parse_search_results():
    result = parse_search_results(SEARCH_HTML, "https://www.eu-startups.com/directory/page/1/")
    assert result.results_count == 1869
    assert result.next_url == "https://www.eu-startups.com/directory/page/2/?foo=bar"
    assert result.listings[0].company_name == "Acme AI"
    assert result.listings[0].based_in == "Berlin"


def test_parse_search_results_finds_rel_next_link():
    html = """
    <html>
      <body>
        <h3>Search Results (20)</h3>
        <div class="wpbdp-listing-excerpt">
          <h3><a href="/directory/acme-ai/">Acme AI</a></h3>
        </div>
        <a rel="next" href="/directory/page/3/?foo=bar">More results</a>
      </body>
    </html>
    """
    result = parse_search_results(html, "https://www.eu-startups.com/directory/page/2/")
    assert result.next_url == "https://www.eu-startups.com/directory/page/3/?foo=bar"


def test_parse_search_results_without_next_link_stops():
    html = """
    <html>
      <body>
        <h3>Search Results (20)</h3>
        <div class="wpbdp-listing-excerpt">
          <h3><a href="/directory/acme-ai/">Acme AI</a></h3>
        </div>
      </body>
    </html>
    """
    result = parse_search_results(html, "https://www.eu-startups.com/directory/page/2/")
    assert result.next_url is None


def test_parse_company_page():
    record = parse_company_page(
        COMPANY_HTML,
        "https://www.eu-startups.com/directory/acme-ai/",
        {"bucket_ranges_eur": {"€1-5 million": {"min": 1_000_000, "max": 5_000_000}}},
    )
    assert record.company_name == "Acme AI"
    assert record.website_url == "https://acme.example"
    assert record.eu_linkedin_url == "https://www.linkedin.com/company/acme-ai/about/"
    assert record.funding_min_eur == 1_000_000
    assert record.description == "Applied AI for logistics."
