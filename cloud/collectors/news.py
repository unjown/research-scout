"""Focused news feeds and deterministic prefilter for Research Scout.

A candidate is not a verified news story. Keep broad PH coverage, narrower
technology/science coverage, and avoid generic health/biology feeds.
"""
import re
from urllib.parse import urlsplit

FEEDS = {
    'technology': 'https://feeds.arstechnica.com/arstechnica/technology-lab',
    'security': 'https://feeds.arstechnica.com/arstechnica/security',
    'philippines': 'https://www.rappler.com/feed/',
    'philippines_technology': 'https://www.rappler.com/technology/feed/',
}

# These are strong negative signals for a *technology digest*, not global
# bans on public-health stories that are major Philippine headlines.
OUT_OF_SCOPE = re.compile(
    r'\b(alzheimer(?:s|\x27s)?|cancer treatment|tumou?r|gene editing|genetic mutation|'
    r'cellular biology|microbiome|gut bacteria|protein folding|clinical trial|'
    r'new species|ancient virus|dementia|nutrition study|vitamin [a-z]\b|'
    r'heart disease study|animal behavior study)\b', re.I)
TECH_SIGNALS = re.compile(
    r'\b(ai|artificial intelligence|machine learning|cybersecurity|cyberattack|'
    r'robot(?:ics)?|chip(?:s)?|semiconductor|software|open.source|'
    r'computer vision|sensor(?:s)?|internet of things|iot|data center|'
    r'quantum computing|satellite|spacecraft|battery|renewable energy|'
    r'engineering|electronics|telecommunications|startup|technology)\b', re.I)
GENERAL_NOISE = re.compile(
    r'\b(movie|celebrity|red carpet|box office|horoscope|gaming review|'
    r'fashion week|sports highlights|reality show)\b', re.I)


def allow_news(title, summary='', source=''):
    """Cheap conservative filter; ambiguous PH stories go to local AI."""
    title = str(title or '')
    summary = str(summary or '')
    text = (title + ' ' + summary)[:2200]
    if not title.strip() or GENERAL_NOISE.search(title):
        return False
    # Philippine headlines and politics are intentionally broad. Do not
    # filter them by tech keywords or impose a political viewpoint.
    if source in ('philippines', 'philippines_technology'):
        return True
    if OUT_OF_SCOPE.search(text) and not TECH_SIGNALS.search(title):
        return False
    return bool(TECH_SIGNALS.search(text))
