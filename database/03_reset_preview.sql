-- READ ONLY. Run first in Supabase SQL Editor. This changes no data.
with delivery as (
  select finding_id,
         bool_or(status = 'sent') as delivered,
         bool_or(status = 'pending') as notification_pending,
         bool_or(status = 'failed') as notification_failed
  from public.local_notification_outbox
  group by finding_id
), active_leases as (
  select distinct finding_id
  from public.local_analysis_items
  where status = 'running' and lease_until > now()
), classified as (
  select f.*,
         coalesce(d.delivered, false) as delivered,
         coalesce(d.notification_pending, false) as notification_pending,
         coalesce(d.notification_failed, false) as notification_failed,
         (a.finding_id is not null) as active_lease,
         (d.finding_id is not null) as has_notification_history
  from public.findings f
  left join delivery d on d.finding_id = f.id
  left join active_leases a on a.finding_id = f.id
  where f.status = 'reviewed'
)
select category, review_status, discord_status,
       delivered, notification_pending, notification_failed, active_lease,
       count(*) as finding_count,
       count(*) filter (where not active_lease and not has_notification_history)
         as default_reset_eligible,
       count(*) filter (where not active_lease)
         as full_reclassification_eligible
from classified
group by category, review_status, discord_status,
         delivered, notification_pending, notification_failed, active_lease
order by category, review_status, discord_status;

-- Inspect actual checks, unique constraints, indexes, and RPC definitions.
select conrelid::regclass as table_name, conname, pg_get_constraintdef(oid) definition
from pg_constraint
where conrelid in (
  'public.findings'::regclass,
  'public.local_analysis_runs'::regclass,
  'public.local_analysis_items'::regclass,
  'public.local_notification_outbox'::regclass,
  'public.local_run_summaries'::regclass
)
order by table_name::text, conname;

select schemaname, tablename, indexname, indexdef
from pg_indexes
where schemaname = 'public' and tablename in (
  'findings', 'local_analysis_runs', 'local_analysis_items',
  'local_notification_outbox', 'local_run_summaries'
)
order by tablename, indexname;

select p.proname, pg_get_functiondef(p.oid) definition
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in ('scout_claim_item', 'scout_complete_item', 'scout_fail_item')
order by p.proname;
