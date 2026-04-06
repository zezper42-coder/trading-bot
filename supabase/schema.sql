create table if not exists bot_control (
  id text primary key,
  bot_enabled boolean not null default true,
  dry_run_override boolean,
  emergency_stop_active boolean not null default false,
  updated_at timestamptz not null default now()
);

create table if not exists strategy_settings (
  theme text primary key,
  enabled boolean not null default true,
  min_surprise double precision,
  min_confidence double precision,
  min_sentiment double precision,
  min_source_count integer,
  confirmation_bars integer,
  volume_multiplier double precision,
  max_event_age_seconds integer,
  risk_per_trade double precision,
  risk_multiplier_min double precision,
  risk_multiplier_max double precision,
  min_trade_score double precision,
  updated_at timestamptz not null default now()
);

create table if not exists cooldowns (
  symbol text primary key,
  cooldown_until timestamptz not null
);

create table if not exists traded_events (
  event_id text primary key
);

create table if not exists managed_positions (
  symbol text primary key,
  asset_class text not null,
  qty double precision not null,
  entry_price double precision not null,
  entry_time timestamptz not null,
  highest_price double precision not null,
  lowest_price double precision not null,
  stop_price double precision not null,
  initial_stop_price double precision not null,
  trailing_active boolean not null default false,
  trailing_stop_price double precision,
  event_id text,
  source text,
  anchor_price double precision,
  actual_value double precision,
  expected_value double precision,
  surprise_score double precision,
  sentiment_score double precision,
  confidence_score double precision,
  source_count integer,
  corroboration_score double precision,
  supporting_sources jsonb not null default '[]'::jsonb,
  target_leverage double precision not null default 1,
  theme text
);

create table if not exists daily_risk_state (
  id text primary key,
  trading_day date,
  daily_start_equity double precision,
  kill_switch_active boolean not null default false,
  updated_at timestamptz not null default now()
);

create table if not exists news_events (
  event_id text primary key,
  source text not null,
  category text not null,
  headline text not null,
  published_at timestamptz not null,
  instrument_scope jsonb not null default '[]'::jsonb,
  supporting_sources jsonb not null default '[]'::jsonb,
  source_count integer not null default 1,
  corroboration_score double precision not null default 1,
  actual_value double precision,
  expected_value double precision,
  surprise_score double precision not null,
  sentiment_score double precision not null,
  confidence_score double precision not null,
  theme text not null default 'general_news',
  topic_tags jsonb not null default '[]'::jsonb,
  entity_tags jsonb not null default '[]'::jsonb,
  direction_score double precision,
  magnitude_score double precision,
  unexpectedness_score double precision,
  trade_score double precision,
  updated_at timestamptz not null default now()
);

create table if not exists signal_evaluations (
  id text primary key,
  timestamp timestamptz not null,
  symbol text not null,
  action text not null,
  reason text not null,
  event_id text,
  source text,
  anchor_price double precision,
  price double precision not null,
  stop_price double precision,
  actual_value double precision,
  expected_value double precision,
  surprise_score double precision,
  sentiment_score double precision,
  confidence_score double precision,
  source_count integer,
  corroboration_score double precision,
  supporting_sources jsonb not null default '[]'::jsonb,
  exit_reason text,
  risk_multiplier double precision,
  risk_per_trade_override double precision,
  theme text,
  topic_tags jsonb not null default '[]'::jsonb,
  entity_tags jsonb not null default '[]'::jsonb,
  direction_score double precision,
  magnitude_score double precision,
  unexpectedness_score double precision,
  trade_score double precision
);

create table if not exists orders (
  order_id text primary key,
  timestamp timestamptz not null,
  symbol text not null,
  side text not null,
  qty double precision,
  notional double precision,
  event_id text,
  source text,
  reason text,
  dry_run boolean not null default false,
  exit_reason text,
  capped_by_buying_power boolean not null default false,
  risk_multiplier double precision,
  risk_per_trade_used double precision,
  theme text,
  trade_score double precision,
  anchor_price double precision,
  price double precision,
  status text not null default 'submitted'
);

create table if not exists position_snapshots (
  symbol text primary key,
  qty double precision not null,
  market_value double precision not null,
  avg_entry_price double precision not null,
  stop_price double precision,
  trailing_stop_price double precision,
  event_id text,
  theme text,
  updated_at timestamptz not null default now()
);

create table if not exists system_heartbeat (
  id text primary key,
  status text not null,
  strategy text not null,
  details jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create index if not exists news_events_published_at_idx on news_events (published_at desc);
create index if not exists news_events_theme_idx on news_events (theme, published_at desc);
create index if not exists signal_evaluations_timestamp_idx on signal_evaluations (timestamp desc);
create index if not exists signal_evaluations_theme_idx on signal_evaluations (theme, timestamp desc);
create index if not exists orders_timestamp_idx on orders (timestamp desc);
create index if not exists orders_symbol_idx on orders (symbol, timestamp desc);

insert into bot_control (id, bot_enabled, dry_run_override, emergency_stop_active)
values ('global', true, null, false)
on conflict (id) do nothing;

insert into daily_risk_state (id, kill_switch_active)
values ('current', false)
on conflict (id) do nothing;
