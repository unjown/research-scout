"""Conservative patent guard; only trusted local policy controls routing."""
from local.policy import load_policy, matches


def patent_guard(item, result, page_text=''):
    if item.get('category') != 'patents':
        return result
    _, policy = load_policy('patents')
    out = dict(result)
    title = str(item.get('title') or '')[:300]
    if matches(policy.get('excluded_title_patterns', []), title) and not matches(policy.get('protected_title_patterns', []), title):
        out.update(score=min(out['score'], 3), destination='archive',
                   needs_verification=False, reason='Excluded specialized biological/pharmaceutical patent.')
        return out
    if out['score'] < policy['minimum_explore_relevance']:
        out.update(destination='archive', needs_verification=False)
    elif out['destination'] == 'patents-main':
        if out['score'] < policy['minimum_main_relevance'] or not (page_text or '').strip():
            out.update(destination='patents-explore',
                       needs_verification=not bool((page_text or '').strip()),
                       reason='Exploratory patent: technical evidence is incomplete or relevance is moderate.')
    return out
