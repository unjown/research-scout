"""Deterministic, category-specific news guard driven by trusted local JSON."""
from local.policy import load_policy, matches


def news_guard(item, result, minimum_score=7):
    if item.get('category') != 'news':
        return result
    _, policy = load_policy('news')
    out = dict(result)
    title = str(item.get('title') or '')[:300]
    excluded = matches(policy.get('excluded_title_patterns', []), title)
    protected = matches(policy.get('protected_title_patterns', []), title)
    if excluded and not protected:
        out.update(score=min(int(out['score']), 3), destination='archive',
                   needs_verification=False,
                   reason='Outside configured daily-news interests; importance may differ from relevance.')
    elif int(out['score']) < max(int(minimum_score), policy['minimum_publish_relevance']):
        out.update(destination='archive', needs_verification=False)
    return out
