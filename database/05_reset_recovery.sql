-- Replace the UUID below with reset_batch_id returned by the reset script.
-- Stop the worker first. Preview the backup rows, then run this transaction.
begin;

create temporary table scout_recovery_parameters on commit drop as
select '00000000-0000-0000-0000-000000000000'::uuid as reset_batch_id;

-- Safety: do not overwrite a finding under an active processing lease.
update public.findings f
set status = b.status,
    ai_score = b.ai_score,
    ai_reason = b.ai_reason,
    ai_category = b.ai_category,
    analyzed_at = b.analyzed_at,
    review_status = b.review_status,
    discord_status = b.discord_status,
    importance_score = b.importance_score,
    evidence_quality = b.evidence_quality,
    verification_status = b.verification_status,
    uncertainty_labels = coalesce(b.uncertainty_labels, '{}')
from public.local_classification_reset_backup b
cross join scout_recovery_parameters p
where b.reset_batch_id = p.reset_batch_id
  and b.finding_id = f.id
  and not exists (
    select 1 from public.local_analysis_items i
    where i.finding_id = f.id and i.status = 'running' and i.lease_until > now()
  );

select p.reset_batch_id, count(*) backup_rows
from public.local_classification_reset_backup b
cross join scout_recovery_parameters p
where b.reset_batch_id = p.reset_batch_id
group by p.reset_batch_id;

commit;
