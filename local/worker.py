"""Approved local analysis with durable completion summaries and bounded retries."""
import logging
import os
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

import requests
from supabase import create_client
from local.classifier import classify
from local.fetcher import fetch_excerpt

log = logging.getLogger('research_scout.worker')
WEBHOOK_KEYS = {
    'opportunities': 'DISCORD_WEBHOOK_OPPORTUNITIES',
    'patents-main': 'DISCORD_WEBHOOK_PATENTS_MAIN',
    'patents-explore': 'DISCORD_WEBHOOK_PATENTS_EXPLORE',
    'daily-news': 'DISCORD_WEBHOOK_DAILY_NEWS',
}


def now():
    return datetime.now(timezone.utc).isoformat()


def database():
    return create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SECRET_KEY'])


def send_webhook(key, message):
    url = os.getenv(key, '').strip()
    if not url:
        raise RuntimeError(f'Missing {key} in local .env')
    # Never log the webhook URL or API credentials.
    for attempt in range(3):
        try:
            response = requests.post(url, json={
                'content': message[:1900], 'allowed_mentions': {'parse': []}
            }, timeout=(5, 15))
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    delay = min(float(response.headers.get('Retry-After', '2') or 2), 10)
                    time.sleep(max(delay, 1))
                    continue
            response.raise_for_status()
            return
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def deliver_summary(db, run_id):
    """Retryable outbox delivery. Best effort at-most-once per observed success.

    A crash after Discord accepts the message but before DB records success can
    produce a duplicate on retry; exactly-once webhook delivery is not possible.
    """
    row = db.table('local_run_summaries').select('*').eq('run_id', run_id).single().execute().data
    if row['delivery_status'] == 'sent':
        return True
    try:
        send_webhook('DISCORD_WEBHOOK_STATUS', row['message'])
    except Exception as exc:
        # Avoid logging webhook URLs or sensitive response bodies.
        message = f'{type(exc).__name__}: status notification failed'
        log.warning('Run #%s final Discord summary failed: %s', run_id, message)
        db.table('local_run_summaries').update({
            'delivery_status': 'failed', 'attempts': row['attempts'] + 1,
            'last_error': message, 'updated_at': now()
        }).eq('run_id', run_id).execute()
        return False
    db.table('local_run_summaries').update({
        'delivery_status': 'sent', 'attempts': row['attempts'] + 1,
        'last_error': None, 'sent_at': now(), 'updated_at': now()
    }).eq('run_id', run_id).execute()
    log.info('Run #%s final Discord summary delivered', run_id)
    return True


def retry_pending_summaries(limit=10):
    """Explicit dashboard action; never starts Ollama."""
    db = database()
    rows = db.table('local_run_summaries').select('run_id').neq('delivery_status', 'sent').order('run_id').limit(limit).execute().data
    results = {'attempted': len(rows), 'sent': 0, 'failed': 0}
    for row in rows:
        try:
            results['sent' if deliver_summary(db, row['run_id']) else 'failed'] += 1
        except Exception:
            log.exception('Could not persist summary delivery for run #%s', row['run_id'])
            results['failed'] += 1
    return results


def log_progress(run_id, index, total, item, counts, outcome):
    log.info('Run #%s | %s/%s | %-17s | %s | #%s %s | published=%d review=%d rejected=%d failed=%d',
             run_id, index, total, outcome, item.get('category', '?'), item.get('id', '?'),
             str(item.get('title', ''))[:65], counts['published'], counts['needs_review'],
             counts['rejected'], counts['failed'])


def deliver_finding_outbox(db, finding_id):
    """Send persisted messages after commit; duplicates remain possible on crash."""
    rows = db.table('local_notification_outbox').select('*').eq('finding_id', finding_id).in_('status', ['pending', 'failed']).execute().data
    for row in rows:
        try:
            send_webhook(WEBHOOK_KEYS[row['destination']], row['content'])
            db.table('local_notification_outbox').update({
                'status': 'sent', 'attempts': row['attempts'] + 1, 'last_error': None,
                'sent_at': now()
            }).eq('id', row['id']).execute()
            db.table('findings').update({'review_status': 'published', 'discord_status': 'published'}).eq('id', finding_id).execute()
            return 'published'
        except Exception as exc:
            log.warning('Notification for finding #%s failed: %s', finding_id, type(exc).__name__)
            db.table('local_notification_outbox').update({
                'status': 'failed', 'attempts': row['attempts'] + 1,
                'last_error': type(exc).__name__
            }).eq('id', row['id']).execute()
            db.table('findings').update({'discord_status': 'failed'}).eq('id', finding_id).execute()
            return 'notification_failed'
    return None


def retry_finding_notifications(limit=20):
    """Explicit retry; no Ollama or webpage fetch."""
    db = database()
    rows = db.table('local_notification_outbox').select('finding_id').in_('status', ['pending','failed']).limit(limit).execute().data
    counts = Counter()
    for row in rows:
        try:
            counts[deliver_finding_outbox(db, row['finding_id']) or 'skipped'] += 1
        except Exception:
            log.exception('Could not retry finding #%s notification', row['finding_id'])
            counts['failed'] += 1
    return dict(counts)


def process_item(db, run_id, item, threshold):
    """Claim and commit result using PostgreSQL RPC transactions."""
    finding_id = item['id']
    token = str(uuid.uuid4())
    claimed = db.rpc('scout_claim_item', {'p_run_id': run_id, 'p_finding_id': finding_id, 'p_token': token}).execute().data
    if not claimed:
        return 'skipped'
    try:
        excerpt = fetch_excerpt(item['url']) if item['category'] == 'patents' else ''
        result = classify(item, excerpt)
        if item['category'] != 'patents' and result['score'] >= 4:
            more = fetch_excerpt(item['url'])
            if more:
                result = classify(item, more)
        score = result['score']
        destination = result['destination']
        review = bool(result.get('needs_verification', True))
        if destination == 'archive' or score < threshold:
            outcome = 'rejected'
        elif review:
            outcome = 'needs_review'
        else:
            outcome = 'publish_candidate'
        message = None
        if outcome == 'publish_candidate':
            message = (f'**{str(item.get("title", "Untitled"))[:150]}**\n'
                       f'{item.get("url", "")}\n{str(result.get("summary", ""))[:280]}\n'
                       f'Relevance: {score}/10')
        committed = db.rpc('scout_complete_item', {
            'p_run_id': run_id, 'p_finding_id': finding_id, 'p_token': token,
            'p_score': score, 'p_reason': str(result.get('reason',''))[:250],
            'p_destination': destination, 'p_outcome': outcome,
            'p_notification': message
        }).execute().data
        if not committed:
            raise RuntimeError('Item lease lost before commit; result not saved')
        if outcome == 'publish_candidate':
            return deliver_finding_outbox(db, finding_id) or 'notification_pending'
        return outcome
    except Exception as exc:
        try:
            db.rpc('scout_fail_item', {
                'p_run_id': run_id, 'p_finding_id': finding_id,
                'p_token': token, 'p_error': f'{type(exc).__name__}: {str(exc)[:250]}'
            }).execute()
        except Exception:
            log.exception('Unable to persist failure for finding #%s', finding_id)
        raise

def run_approved(run_id):
    """Finish a run even if individual items fail; always attempt a durable summary."""
    db = None
    counts = Counter()
    total = 0
    fatal = None
    status = 'failed'
    try:
        db = database()
        run = db.table('local_analysis_runs').select('*').eq('id', run_id).single().execute().data
        rows = db.table('local_analysis_items').select('finding_id,status').eq('run_id', run_id).execute().data
        total = len(rows)
        db.table('local_analysis_runs').update({'status': 'running', 'started_at': now()}).eq('id', run_id).execute()
        log.info('Run #%s START | total=%d threshold=%d', run_id, total, run['minimum_score'])
        for index, row in enumerate(rows, 1):
            if row['status'] == 'completed':
                counts['previously_completed'] += 1
                continue
            fid = row['finding_id']
            item = {'id': fid, 'category': '?', 'title': '(loading)'}
            try:
                item = db.table('findings').select('*').eq('id', fid).single().execute().data
                log_progress(run_id, index, total, item, counts, 'START')
                outcome = process_item(db, run_id, item, run['minimum_score'])
                counts[outcome] += 1
            except Exception as exc:
                outcome = 'failed'
                counts['failed'] += 1
                log.exception('Run #%s item #%s failed', run_id, fid)
                # process_item already attempts an atomic failure update.

            log_progress(run_id, index, total, item, counts, outcome.upper())
        status = 'completed_with_errors' if counts['failed'] or counts['notification_failed'] else 'completed'
    except Exception as exc:
        fatal = f'{type(exc).__name__}: {str(exc)[:250]}'
        log.exception('Run #%s fatal error', run_id)
    finally:
        summary = (f'**Research Scout — Quick Mode #{run_id}**\n'
                   f'Status: {status}\n'
                   f'Total: {total} | Analyzed this attempt: {sum(counts[k] for k in ("published","needs_review","rejected","notification_failed","failed"))}\n'
                   f'Published: {counts["published"]} | Needs review: {counts["needs_review"]}\n'
                   f'Rejected: {counts["rejected"]} | Failed: {counts["failed"]}\n'
                   f'Notification failures: {counts["notification_failed"]} | Previously completed: {counts["previously_completed"]}')
        log.info('Run #%s FINAL: %s', run_id, summary.replace('\n', ' | '))
        if db is not None:
            try:
                # If an unexpected fatal error occurred, mark the run failed.
                db.table('local_analysis_runs').update({
                    'status': status if status != 'completed_with_errors' else 'failed',
                    'finished_at': now(), 'error_message': fatal or (
                        'Some findings or notifications failed' if status == 'completed_with_errors' else None)
                }).eq('id', run_id).execute()
            except Exception:
                log.exception('Run #%s could not persist final run status', run_id)
            try:
                db.table('local_run_summaries').upsert({
                    'run_id': run_id, 'message': summary, 'delivery_status': 'pending',
                    'updated_at': now()
                }, on_conflict='run_id').execute()
                deliver_summary(db, run_id)
            except Exception:
                log.exception('Run #%s could not queue/deliver final summary; verify phase3_reliability.sql', run_id)
    return {'run_id': run_id, 'status': status, 'counts': dict(counts)}
