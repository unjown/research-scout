"""Daily metadata discovery. Never uses local AI or exposes local Ollama."""
import os
import sys
import logging
import requests
import feedparser
from dotenv import load_dotenv
from cloud.collectors.opportunities import QUERIES as OPPORTUNITY_QUERIES
from cloud.collectors.patents import QUERIES as PATENT_QUERIES
from cloud.collectors.news import FEEDS, allow_news
from cloud.filters import normalize
from cloud.database import save_findings
from cloud.discord import send_status

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
load_dotenv()


def tavily(query, category):
    key = os.getenv('TAVILY_API_KEY')
    if not key:
        logging.warning('No Tavily key; skipping %s', query)
        return []
    r = requests.post('https://api.tavily.com/search', headers={'Authorization': f'Bearer {key}'}, json={'query': query, 'search_depth': 'basic', 'max_results': 5}, timeout=40)
    r.raise_for_status()
    out = []
    for hit in r.json().get('results', []):
        record = normalize(hit.get('title'), hit.get('url'), hit.get('content'), category, 'Tavily')
        if record:
            out.append(record)
    return out


def rss_news():
    out = []
    for source, url in FEEDS.items():
        try:
            feed = feedparser.parse(requests.get(url, timeout=30).content)
            for entry in feed.entries[:10]:
                record = normalize(entry.get('title'), entry.get('link'), entry.get('summary'), 'news', source)
                if record and allow_news(record['title'], record['summary'], source):
                    out.append(record)
        except Exception as exc:
            logging.warning('RSS %s failed: %s', source, exc)
    return out


def main():
    items = []
    for category, queries in [('opportunities', OPPORTUNITY_QUERIES), ('patents', PATENT_QUERIES)]:
        for query in queries:
            try:
                items.extend(tavily(query, category))
            except Exception as exc:
                logging.warning('Search failed (%s): %s', category, exc)
    items.extend(rss_news())
    deduped = {item['url']: item for item in items}
    logging.info('Discovered %s unique candidates', len(deduped))
    count = save_findings(list(deduped.values()))
    # This is a candidate count, not the count of newly inserted rows.
    send_status(f'RS.ai discovery finished. {count} unique candidates submitted to database. Local AI remains off. Eligibility and patent status are not yet verified.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        logging.exception('Discovery failed')
        try:
            send_status('RS.ai discovery FAILED; inspect GitHub Actions logs.')
        except Exception:
            pass
        sys.exit(1)
