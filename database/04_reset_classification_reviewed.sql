-- DESTRUCTIVE, REVIEWED SCRIPT. DO NOT RUN WITHOUT EXPLICIT USER APPROVAL.
-- Default mode resets only classified findings with NO notification history.
-- For explicitly approved full reclassification, change full_mode to true.
-- Full mode still preserves all outbox rows; the worker suppresses reposts.
begin;

create temporary table scout_reset_parameters on commit drop as
select gen_random_uuid() as reset_batch_id, false::boolean as full_mode;

create temporary table scout_reset_targets on commit drop as
select f.id
from public.findings f
cross join scout_reset_parameters p
where f.status = 'reviewed'
  and not exists (
    select 1 from public.local_analysis_items i
    where i.finding_id = f.id
      and i.status = 'running'
      and i.lease_until > now()
  )
  and (
    p.full_mode
    or not exists (
      select 1 from public.local_notification_outbox o
      where o.finding_id = f.id
    )
  );

-- Review the transaction-local target set before the update result below.
select f.category, f.review_status, f.discord_status, count(*) affected
from public.findings f join scout_reset_targets t on t.id = f.id
group by f.category, f.review_status, f.discord_status
order by f.category, f.review_status, f.discord_status;

insert into public.local_classification_reset_backup (
  reset_batch_id, finding_id, status, ai_score, ai_reason, ai_category,
  analyzed_at, review_status, discord_status, importance_score,
  evidence_quality, verification_status, uncertainty_labels
)
select p.reset_batch_id, f.id, f.status, f.ai_score, f.ai_reason, f.ai_category,
       f.analyzed_at, f.review_status, f.discord_status, f.importance_score,
       f.evidence_quality, f.verification_status, f.uncertainty_labels
from public.findings f
join scout_reset_targets t on t.id = f.id
cross join scout_reset_parameters p;

update public.findings f
set status = 'pending',
    ai_score = null,
    ai_reason = null,
    ai_category = null,
    analyzed_at = null,
    review_status = null,
    discord_status = null,
    importance_score = null,
    evidence_quality = null,
    verification_status = null,
    uncertainty_labels = '{}'
from scout_reset_targets t
where f.id = t.id;

select p.reset_batch_id, p.full_mode, count(*) reset_count
from scout_reset_targets t cross join scout_reset_parameters p
group by p.reset_batch_id, p.full_mode;

commit;
