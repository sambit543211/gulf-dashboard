-- Gulf Pulse database setup. Paste into Supabase → project "gulf-pulse" → SQL Editor → New query → Run.

-- Published editions: one row per day, always the latest version. Public read.
create table public.editions (
  date date primary key,
  version int not null,
  published_at timestamptz not null default now(),
  data jsonb not null
);
-- Every published version, kept for history and rollback. Not publicly readable.
create table public.edition_versions (
  date date not null,
  version int not null,
  published_at timestamptz not null default now(),
  data jsonb not null,
  primary key (date, version)
);
-- Phones/browsers that turned on notifications.
create table public.push_subs (
  endpoint text primary key,
  p256dh text not null,
  auth text not null,
  created_at timestamptz not null default now()
);
-- Server-only settings (the publish secret). No policies, so the public key can never read it.
create table public.app_config (k text primary key, v text not null);

alter table public.editions enable row level security;
alter table public.edition_versions enable row level security;
alter table public.push_subs enable row level security;
alter table public.app_config enable row level security;

create policy "Anyone can read published editions" on public.editions for select using (true);

create view public.edition_index with (security_invoker = true) as
  select date, version, published_at,
         jsonb_array_length(data->'stories') as stories,
         (select count(*) from jsonb_array_elements(data->'stories') s where s->>'section' = 'Gulf in the News')::int as gulf
  from public.editions;

create or replace function public._check_secret(p_secret text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if p_secret is null or p_secret is distinct from (select v from app_config where k = 'publish_secret') then
    raise exception 'forbidden' using errcode = '42501';
  end if;
end $$;

create or replace function public.save_edition(p_secret text, p_date date, p_version int, p_data jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform _check_secret(p_secret);
  insert into edition_versions(date, version, data) values (p_date, p_version, p_data);
  insert into editions(date, version, data, published_at) values (p_date, p_version, p_data, now())
  on conflict (date) do update set version = excluded.version, data = excluded.data, published_at = now();
end $$;

create or replace function public.add_push_sub(p_endpoint text, p_p256dh text, p_auth text) returns void
language sql security definer set search_path = public as $$
  insert into push_subs(endpoint, p256dh, auth) values (p_endpoint, p_p256dh, p_auth)
  on conflict (endpoint) do update set p256dh = excluded.p256dh, auth = excluded.auth;
$$;

create or replace function public.list_push_subs(p_secret text) returns setof public.push_subs
language plpgsql security definer set search_path = public as $$
begin
  perform _check_secret(p_secret);
  return query select * from push_subs;
end $$;

create or replace function public.remove_push_sub(p_secret text, p_endpoint text) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform _check_secret(p_secret);
  delete from push_subs where endpoint = p_endpoint;
end $$;

revoke execute on function public._check_secret(text) from public, anon, authenticated;
