"""Deterministic guardrail for local Qwen news classifications."""
import re

BIOLOGY = re.compile(r'\b(alzheimer|dementia|cancer|tumou?r|gene|genetic|'
                     r'protein|microbiome|gut bacteria|nutrition|vitamin|'
                     r'cell biology|clinical trial|animal species)\b', re.I)
TECH = re.compile(r'\b(ai|artificial intelligence|robot|computer vision|'
                  r'cybersecurity|software|semiconductor|chip|sensor|'
                  r'engineering|electronics|energy storage|battery|'
                  r'quantum computing|technology|iot)\b', re.I)

def news_guard(item, result, minimum_score=7):
    if item.get('category') != 'news':
        return result
    result = dict(result)
    score = int(result.get('score', 0))
    text = str(item.get('title', '')) + ' ' + str(item.get('summary', ''))
    source = str(item.get('source_name', ''))
    # A low score must never be promoted to a review queue merely because
    # the model is uncertain about verification.
    if score < minimum_score or result.get('destination') == 'archive':
        result['destination'] = 'archive'
        result['needs_verification'] = False
        return result
    # Biology is not categorically banned from PH headlines; major public
    # stories should remain reviewable. Non-PH science/health gets screened.
    if source not in ('philippines', 'philippines_technology') and BIOLOGY.search(text) and not TECH.search(item.get('title','')):
        result['destination'] = 'archive'
        result['score'] = min(score, 3)
        result['needs_verification'] = False
        result['reason'] = 'Outside selected news categories (general biology/medical research).'
        return result
    result['destination'] = 'daily-news'
    return result
