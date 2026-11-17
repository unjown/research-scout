"""Conservative patent relevance guard; no API keys or network requests."""
import re

EXCLUDE = re.compile(r'\b(?:gene editing|genetic engineering|gene therapy|genome|crispr|dna sequencing|rna sequencing|pharmaceutical|drug formulation|protein engineering|cancer therapy|surgical implant|clinical treatment)\b', re.I)
GENERAL = re.compile(r'\b(?:humanoid robot|computer vision systems?|iot sensors?|artificial intelligence systems?|machine learning systems?)\b', re.I)
SPECIFIC = re.compile(r'\b(?:low.cost|embedded|microcontroller|edge computing|on.device|open.source|camera calibration|lidar|mmwave|radar|sensor fusion|wireless sensing|computer vision|robot navigation|assistive|energy harvesting|fault detection|object detection|image processing)\b', re.I)

def patent_guard(item, result, page_text=""):
    """Modify AI classification only for clear evidence; uncertainty goes to Explore."""
    if item.get('category') != 'patents':
        return result
    text = ' '.join(str(item.get(k) or '') for k in ('title', 'summary')) + ' ' + page_text[:5000]
    if EXCLUDE.search(text) and not SPECIFIC.search(text):
        result.update(destination='archive', score=min(result['score'], 2),
                      reason='Specialized biological/medical invention outside accessible prototyping scope.',
                      needs_verification=True)
    elif result['destination'] == 'patents-main' and (not page_text.strip() or (GENERAL.search(item.get('title', '')) and not SPECIFIC.search(text))):
        result.update(destination='patents-explore', score=min(result['score'], 6),
                      reason='Patent specificity or accessible implementation not yet established.',
                      needs_verification=True)
    return result
