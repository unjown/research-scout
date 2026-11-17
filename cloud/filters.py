from urllib.parse import urlsplit, urlunsplit


def clean_url(url):
    try:
        p = urlsplit(url.strip())
        if p.scheme not in ('https', 'http') or not p.netloc:
            return None
        return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip('/') or '/', '', ''))
    except (ValueError, AttributeError):
        return None


def normalize(title, url, summary, category, source):
    canonical = clean_url(url)
    if not canonical or not title:
        return None
    return {'title': str(title)[:500], 'url': canonical, 'summary': str(summary or '')[:1800], 'category': category, 'source_name': source}
