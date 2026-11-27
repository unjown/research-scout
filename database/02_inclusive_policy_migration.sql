-- Add orthogonal classification fields without changing source discovery data.
begin;

alter table public.findings
  add column if not exists importance_score integer,
  add column if not exists evidence_quality integer,
  add column if not exists verification_status text,
  add column if not exists uncertainty_labels text[] not null default '{}';

alter table public.findings drop constraint if exists findings_importance_score_range;
alter table public.findings add constraint findings_importance_score_range
  check (importance_score is null or importance_score between 0 and 10);
alter table public.findings drop constraint if exists findings_evidence_quality_range;
alter table public.findings add constraint findings_evidence_quality_range
  check (evidence_quality is null or evidence_quality between 0 and 10);

-- One durable publication history per finding prevents reset-driven reposts.
create unique index if not exists local_notification_outbox_one_finding
  on public.local_notification_outbox (finding_id);

create table if not exists public.local_classification_reset_backup (
  reset_batch_id uuid not null,
  backed_up_at timestamptz not null default now(),
  finding_id bigint not null references public.findings(id),
  status text,
  ai_score integer,
  ai_reason text,
  ai_category text,
  analyzed_at timestamptz,
  review_status text,
  discord_status text,
  importance_score integer,
  evidence_quality integer,
  verification_status text,
  uncertainty_labels text[],
  primary key (reset_batch_id, finding_id)
);

comment on table public.local_classification_reset_backup is
  'Recovery snapshots created by reviewed reset scripts; never used as discovery input.';

commit;
