# Trading Bot

En event-drevet paper trading-bot for `TSLA`, `BTC/USD`, `Trump/Oil`-proxyer, earnings på `US small/mid-cap`-aksjer og norske aksjer. `Alpaca` brukes for US-aksjer/krypto, og `Saxo` kan brukes for norske aksjer på `XOSL`.

Bruk `./run-bot ...` i stedet for `uv run trading-bot ...`. Wrapperen tvinger `--no-editable`, som gjør kjøringen stabil i denne mappen.

## Hva v1 gjør

- Kjører strategien `news_shock` som standard
- Handler på strukturerte overraskelser, og for `BTC/USD` også på mindre, uventede positive nyheter
- Har en ny `oil_policy`-profil for Trump/White House-relaterte oljeheadlines, med proxy-symboler som `USO`, `XLE`, `OXY`, `XOM`, `CVX` og `SLB`
- Har en egen `earnings_surprise`-strategi for flere hundre `US small/mid-cap`-aksjer
- Bruker:
  - `Alpaca` for prisdata, konto, ordre og headline-kontekst
  - `Finnhub` eller lokal JSON-feed for `actual vs expected`-hendelser
  - `SEC EDGAR` som sekundær kilde for filing-freshness i earnings-scan
- Går `long` på positive signaler, og kan `shorte` negative `TSLA`-nyheter
- Bruker kort prisbekreftelse før entry
- Bruker `1.5 x ATR(14)` hard stop, trailing stop og `momentum fade`-exit for `BTC/USD`
- Flater `TSLA` før børsstenging og `BTC/USD` etter maks holdetid
- Sender Telegram-varsler for ordre og daglig earnings-watchlist
- Har et passordbeskyttet dashboard på `/dashboard` med live events, posisjoner, ordre, risk settings og nødknapper
- Kan bruke `Supabase` som persistent kontrollplan for `stop bot`, `dry run`, `nødselg`, settings og audit-feed
- Logger signaler og ordre til JSONL, og earnings-snapshots/releases til SQLite

## Kom i gang

1. Installer avhengigheter:

```bash
uv sync --no-editable --extra dev
```

2. Fyll inn broker-nøkler i `.env`.

For `Alpaca`:

- `BROKER_KIND=alpaca`
- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`

For `Saxo`:

- `BROKER_KIND=saxo`
- `SAXO_ACCESS_TOKEN`
- `SAXO_ENVIRONMENT=sim`
- `SAXO_DEFAULT_EXCHANGE_ID=XOSL`

For `Telegram` ordrevarsler:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_WEBHOOK_SECRET` hvis du vil bruke Telegram webhook-chatten
- valgfritt `TELEGRAM_DISABLE_NOTIFICATION=true`
- valgfritt `TELEGRAM_MESSAGE_THREAD_ID` hvis du bruker forum topic i gruppe
- `OPENAI_API_KEY` og valgfritt `OPENAI_MODEL` hvis Telegram-boten også skal svare på fritekst

For dashboard og persistent kontrollstate:

- `DASHBOARD_ADMIN_PASSWORD`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- valgfritt `SUPABASE_ANON_KEY`

Opprett tabellene først:

```bash
psql "$SUPABASE_DB_URL" -f supabase/schema.sql
```

eller lim inn [schema.sql](/Users/jesperhammersvik/Documents/Trading%20bot/supabase/schema.sql) i Supabase SQL editor.

3. Bruk file-replay først:

```bash
./run-bot backtest --symbol TSLA --asset-class stock --days 5
./run-bot backtest --symbol BTC/USD --asset-class crypto --days 5
```

4. Kjør en live paper-runde:

```bash
./run-bot run-once
```

5. Kjør kontinuerlig:

```bash
./run-bot run-paper
```

## Dashboard

Dashboardet ligger på samme Vercel-domene som webhookene:

```text
https://<deployment>.vercel.app/dashboard
```

Det viser:

- konto, equity, cash, buying power og heartbeat
- åpne posisjoner med stop, trailing og event-id
- live nyhetsfeed med tema, scope og `trade_score`
- signaler og ordre
- risk/settings per tema: `btc_news`, `tsla_news`, `oil_policy`, `earnings_surprise`

Knapper i UI:

- `Stopp bot`
- `Resume`
- `Dry run`
- `Nødselg`
- `Kanseller pending`
- `Kjør news eval nå`
- `Kjør scan nå`

`DASHBOARD_ADMIN_PASSWORD` brukes til enkel single-user auth. Endepunktene under `/api/ui/*` krever signert `HttpOnly`-cookie.

## 24/7 På VPS

Dette prosjektet er nå gjort klart for en liten Linux-server, f.eks. en `Oracle Cloud Always Free` VM.

Filer:

- [bootstrap.sh](/Users/jesperhammersvik/Documents/Trading%20bot/deploy/server/bootstrap.sh)
- [run-service.sh](/Users/jesperhammersvik/Documents/Trading%20bot/deploy/server/run-service.sh)
- [install-systemd.sh](/Users/jesperhammersvik/Documents/Trading%20bot/deploy/server/install-systemd.sh)
- [update.sh](/Users/jesperhammersvik/Documents/Trading%20bot/deploy/server/update.sh)
- [healthcheck.sh](/Users/jesperhammersvik/Documents/Trading%20bot/deploy/server/healthcheck.sh)

Anbefalt flyt på en Ubuntu- eller Oracle Linux-VM:

1. Kopier repoet til serveren, f.eks. til `/opt/trading-bot`
2. Legg inn en server-egen `.env`
3. Kjør:

```bash
cd /opt/trading-bot
./deploy/server/bootstrap.sh
```

4. Sett riktig runtime-modus i `.env`:

```env
BOT_COMMAND=run-paper
BOT_LOG_LEVEL=INFO
BOT_DRY_RUN=false
```

Hvis du heller vil kjøre earnings-boten kontinuerlig:

```env
BOT_COMMAND=run-earnings
```

5. Installer og start systemd-servicen:

```bash
sudo ./deploy/server/install-systemd.sh
```

6. Sjekk status:

```bash
./deploy/server/healthcheck.sh
```

Nyttige driftkommandoer:

```bash
sudo systemctl restart trading-bot.service
sudo journalctl -u trading-bot.service -n 100 --no-pager
./deploy/server/update.sh
```

Standardservice:

- bruker repoets `.env`
- restarter automatisk ved krasj
- starter ved boot
- kjører `BOT_COMMAND` fra `.env`

Hvis du vil kjøre flere botter senere, er enkleste vei å kopiere repoet til separate mapper og installere én `systemd`-service per mappe.

Hvis Alpaca-kontoen din ikke har tilgang til nylig `SIP`-data for aksjer, bruk `ALPACA_STOCK_FEED=iex`. Det er satt som standard.

## Earnings Surprise

`earnings_surprise` bygger et univers av kommende earnings de neste `7` dagene, filtrerer på:

- `US common stocks`
- `300M` til `10B` market cap
- pris minst `3 USD`
- minst `2M USD` i 30-dagers gjennomsnittlig dollarvolum
- ikke `ETF`, `ADR`, `SPAC`, `OTC`, warrants eller units

Scanneren rangerer kandidater med:

- consensus snapshots lagret i SQLite
- historisk earnings-surprise-kvalitet
- SEC filing-freshness
- likviditet og volatilitet

Når en release faktisk kommer, kjøper den bare hvis:

- både `EPS` og `revenue` slår consensus
- releasen er fersk nok
- de siste `2` ettminuttsbarene er over anchor-prisen
- volumet bekrefter, eller volumhistorikken er for tynn i extended hours

Exit skjer via:

- `2.0 x ATR(14)` hard stop
- trailing stop fra `+3%`
- `momentum fade`
- flatten `10` minutter før relevant regular close

Risikoen justeres nå også ut fra hvor mye resultatet slår forventning:

- basisrisiko styres fortsatt av `EARNINGS_RISK_PER_TRADE`
- signalet får en `risk_multiplier` basert på hvor sterk `EPS`- og `revenue`-surprisen er relativt til minstekravene
- sterkere beat gir større størrelse
- multiplikatoren capes av:
  - `EARNINGS_RISK_MULTIPLIER_MIN`
  - `EARNINGS_RISK_MULTIPLIER_MAX`

Standardformelen vekter:

- `65%` EPS-surprise
- `35%` revenue-surprise

Kommandoer:

```bash
./run-bot scan-earnings
./run-bot run-earnings
./run-bot backtest-earnings --from 2026-01-01 --to 2026-03-31
```

Hvis du vil bruke earnings som hovedstrategi i `run-once` eller `run-paper`, sett:

```env
BOT_STRATEGY=earnings_surprise
```

Viktig:

- Finnhub-kontoen din mangler enkelte premium-estimate-endepunkter. Boten degraderer derfor trygt til `calendar/earnings` + historiske surprises + SEC-freshness i stedet for å krasje.
- For best resultat mot SEC bør `SEC_API_USER_AGENT` settes til noe mer spesifikt enn standarden, f.eks. navn og e-post.

## Standardoppsett

`.env` er nå satt opp rundt disse standardene:

- `BOT_STRATEGY=news_shock`
- `BOT_SYMBOLS=TSLA:stock,BTC/USD:crypto`
- `SURPRISE_PROVIDER=finnhub`
- `STRUCTURED_EVENTS_PATH=examples/structured_events.json`
- `BOT_DRY_RUN=true`

Det betyr at boten kan testes uten Finnhub-nøkkel ved å bruke replay-filen.

For earnings kan du la `BOT_STRATEGY` stå som `news_shock` og likevel kjøre `scan-earnings` / `run-earnings`, siden de kommandoene bruker earnings-pipelinen direkte.

## Structured events

`news_shock` leser denne formen:

```json
[
  {
    "event_id": "evt-1",
    "source": "finnhub",
    "instrument_scope": ["TSLA"],
    "category": "earnings",
    "published_at": "2026-04-04T12:20:00Z",
    "headline": "TSLA beats expectations",
    "actual_value": 1.5,
    "expected_value": 1.2,
    "surprise_score": 0.8,
    "sentiment_score": 0.3,
    "confidence_score": 0.9,
    "is_scheduled": true
  }
]
```

Relevante env-variabler:

- `BROKER_KIND`
- `SURPRISE_PROVIDER`
- `FINNHUB_API_KEY`
- `FINNHUB_WEBHOOK_SECRET`
- `SAXO_ACCESS_TOKEN`
- `SAXO_ENVIRONMENT`
- `SAXO_ACCOUNT_KEY`
- `SAXO_DEFAULT_EXCHANGE_ID`
- `SAXO_INSTRUMENT_MAP`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_DISABLE_NOTIFICATION`
- `TELEGRAM_MESSAGE_THREAD_ID`
- `STRUCTURED_EVENTS_PATH`
- `NEWS_SHOCK_MIN_SURPRISE`
- `NEWS_SHOCK_MIN_CONFIDENCE`
- `NEWS_SHOCK_MIN_SENTIMENT`
- `NEWS_SHOCK_CONFIRMATION_BARS`
- `NEWS_SHOCK_VOLUME_MULTIPLIER`
- `NEWS_SHOCK_MAX_EVENT_AGE_SECONDS`
- `NEWS_SHOCK_BTC_MAX_HOLD_MINUTES`
- `NEWS_SHOCK_STOCK_FLATTEN_MINUTES_BEFORE_CLOSE`
- `NEWS_SHOCK_TARGET_LEVERAGE`
- `NEWS_SHOCK_BTC_MIN_SURPRISE`
- `NEWS_SHOCK_BTC_MIN_CONFIDENCE`
- `NEWS_SHOCK_BTC_MIN_SENTIMENT`
- `NEWS_SHOCK_BTC_MIN_SOURCE_COUNT`
- `NEWS_SHOCK_BTC_CONFIRMATION_BARS`
- `NEWS_SHOCK_BTC_VOLUME_MULTIPLIER`
- `NEWS_SHOCK_BTC_MOMENTUM_FADE_BARS`
- `NEWS_SHOCK_BTC_MOMENTUM_FADE_MIN_PROFIT_PCT`
- `NEWS_SHOCK_BTC_MOMENTUM_FADE_FROM_HIGH_PCT`
- `RISK_MAX_DAILY_LOSS_PCT`
- `TRADE_LOG_PATH`
- `EARNINGS_PROVIDER`
- `EARNINGS_LOOKAHEAD_DAYS`
- `EARNINGS_UNIVERSE_MAX_SIZE`
- `EARNINGS_MARKET_CAP_MIN_USD`
- `EARNINGS_MARKET_CAP_MAX_USD`
- `EARNINGS_MIN_PRICE_USD`
- `EARNINGS_MIN_AVG_DOLLAR_VOLUME_USD`
- `EARNINGS_MIN_EPS_SURPRISE_PCT`
- `EARNINGS_MIN_REVENUE_SURPRISE_PCT`
- `EARNINGS_MAX_EVENT_AGE_SECONDS`
- `EARNINGS_CONFIRMATION_BARS`
- `EARNINGS_VOLUME_MULTIPLIER`
- `EARNINGS_RISK_PER_TRADE`
- `EARNINGS_RISK_MULTIPLIER_MIN`
- `EARNINGS_RISK_MULTIPLIER_MAX`
- `EARNINGS_MAX_OPEN_POSITIONS`
- `EARNINGS_MAX_DAILY_LOSS_PCT`
- `EARNINGS_WATCHLIST_LIMIT`
- `EARNINGS_TELEGRAM_WATCHLIST_ENABLED`
- `EARNINGS_DB_PATH`
- `VERCEL_WEBHOOK_LOGS_ENABLED`
- `VERCEL_WEBHOOK_SCOPE`
- `VERCEL_WEBHOOK_ENVIRONMENT`
- `VERCEL_WEBHOOK_LOGS_SINCE_MINUTES`
- `OFFICIAL_RSS_FEEDS_ENABLED`
- `OFFICIAL_RSS_FEED_URLS`
- `SEC_TSLA_SUBMISSIONS_ENABLED`
- `SEC_API_USER_AGENT`
- `X_WEBHOOK_ENABLED`
- `X_CONSUMER_SECRET`
- `X_WEBHOOK_URL`
- `X_FILTERED_STREAM_RULE`
- `X_FILTERED_STREAM_RULE_TAG`
- `X_STREAM_ENABLED`
- `X_STREAM_CONNECT_TIMEOUT_SECONDS`
- `X_STREAM_READ_TIMEOUT_SECONDS`
- `X_STREAM_MAX_BACKOFF_SECONDS`
- `X_RECENT_SEARCH_ENABLED`
- `X_BEARER_TOKEN`
- `X_RECENT_SEARCH_QUERY`
- `X_RECENT_SEARCH_MAX_RESULTS`
- `NEWS_SHOCK_MIN_SOURCE_COUNT`
- `NEWS_SHOCK_REALTIME_WINDOW_SECONDS`

## Design

- `strategy.py`: `news_shock` entry/exit-logikk, ATR og trailing stop
- `surprise_provider.py`: Finnhub-provider, official feed-ingestion og event-joiner
- `surprise_provider.py`: Finnhub, X recent search, official feed-ingestion og event-joiner
- `event_feed.py`: lokal replay-feed for structured events
- `official_feeds.py`: offisielle RSS- og SEC-submissions-feeds
- `risk.py`: risk-based sizing med buying-power cap
- `bot.py`: runtime-state, cooldown, kill switch, ordre- og signalflyt
- `state_store.py`: Supabase-backed persistent state for controlplane, audit og UI
- `dashboard.py`: auth-cookie, dashboard-HTML og `/api/ui/*`-payloads
- `earnings_provider.py`: Finnhub/SEC-ingest, universfilter og pre-earnings-scoring
- `earnings_bot.py`: daglig scan, watchlist, live earnings-runner og replay
- `adapters/alpaca.py`: Alpaca bars, konto, ordre og headlines
- `x_webhooks.py`: X webhook-oppsett og filtered-stream-linking
- `x_stream.py`: vedvarende X filtered-stream-worker for low-latency trades
- `adapters/saxo.py`: Saxo OpenAPI for norske aksjer
- `persistence.py`: JSONL-logg og SQLite for earnings-univers, consensus, releases og trades
- `api/index.py`: Vercel webhook-mottaker for Finnhub
- `webhook_bridge.py`: normalisering av Finnhub-webhooks og parsing av Vercel runtime-logs

## Begrensninger i v1

- `BTC/USD` er fortsatt long-only på Alpaca
- `X`/tweets krever bearer token + consumer secret for ekte push/webhook
- Ingen sosial strategi i produksjonsløpet
- Ingen reell `10x` execution på Alpaca
- `target_leverage=10` lagres som strategiintensjon, men ordre capes til Alpaca-buying-power
- `news_shock` krever som standard minst `2` kilder på samme sak før entry
- `SaxoBroker` støtter foreløpig bare aksjer, ikke krypto
- Hvis du vil kjøre `BTC` på `Alpaca` og norske aksjer på `Saxo`, gjør det som separate bot-runs i denne versjonen

## Telegram Varsler

Hvis `TELEGRAM_BOT_TOKEN` og `TELEGRAM_CHAT_ID` er satt, sender boten en Telegram-melding for hver `BUY`- og `SELL`-ordre.

Meldingen inkluderer:

- side og instrument
- tid, pris, qty og notional
- event-id og kilde når tilgjengelig
- stop/anchor og exit reason når relevant
- om ordren ble capet av buying power
- om hendelsen var `DRY RUN`

## Telegram Chat Webhook

Det finnes nå også en egen Telegram-chat-endpoint på:

```text
https://<deployment>.vercel.app/api/telegram
```

Den gjør dette:

- tar imot meldinger fra Telegram webhook
- svarer tilbake i samme chat
- bruker lokale kommandoer som `help`, `status`, `id`
- bruker OpenAI Responses API for fritekst hvis `OPENAI_API_KEY` er satt på Vercel

Relevante env-variabler:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

Webhooken kan registreres slik:

```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://<deployment>.vercel.app/api/telegram" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

Hvis `OPENAI_API_KEY` mangler, svarer Telegram-boten fortsatt på lokale kommandoer og forteller at AI-chat ikke er aktivert ennå.

## Saxo For Norske Aksjer

Eksempel:

```env
BROKER_KIND=saxo
SAXO_ACCESS_TOKEN=<token>
SAXO_ENVIRONMENT=sim
SAXO_DEFAULT_EXCHANGE_ID=XOSL
BOT_SYMBOLS=EQNR:stock,ORK:stock
```

Hvis symboloppslag blir tvetydig, kan du låse UIC direkte:

```env
SAXO_INSTRUMENT_MAP=EQNR=12345,ORK=67890
```

`SaxoBroker` henter:

- konto via `Portfolio/Balances`
- posisjoner via `Portfolio/NetPositions`
- bars via `Chart/Charts`
- market orders via `Trade/Orders`

## Kilder

- [Alpaca Trading API](https://docs.alpaca.markets/docs/trading-api)
- [Alpaca Margin and Short Selling](https://docs.alpaca.markets/docs/margin-and-short-selling)
- [Alpaca Crypto Spot Trading](https://docs.alpaca.markets/docs/crypto-trading)
- [Alpaca Historical News Data](https://docs.alpaca.markets/v1.3/docs/historical-news-data)
- [Alpaca Real-time News](https://docs.alpaca.markets/docs/streaming-real-time-news)
- [Saxo OpenAPI Learn](https://developer.saxobank.com/openapi/learn)
- [Saxo Chart API](https://developer.saxobank.com/openapi/referencedocs/chart/v3/charts/get__chart)
- [Saxo Orders API](https://developer.saxobank.com/openapi/referencedocs/trade/v2/orders/post__trade)

## Finnhub webhook

Når Vercel-endpointet er deployet, skal `Webhook URL` i Finnhub være:

```text
https://<deployment>.vercel.app/api
```

`Secret` i Finnhub blir generert av Finnhub og må matche `FINNHUB_WEBHOOK_SECRET` på Vercel. Endpointet:

- verifiserer `X-Finnhub-Secret`
- validerer at body er JSON
- returnerer `204`
- logger normaliserte `StructuredEvent`-records i Vercel runtime-logs

Når `VERCEL_WEBHOOK_LOGS_ENABLED=true`, poller den lokale boten disse runtime-loggene via `vercel logs` og bruker dem som live `news_shock`-feed.

På Vercel serverless kjører webhooken nå også en umiddelbar `news_shock`-vurdering direkte når et relevant Finnhub-event kommer inn. Det betyr at du ikke trenger en lokal prosess bare for BTC/TSLA nyhetsreaksjoner.

## X real-time stream

Hvis du vil kjøpe på sekunder i stedet for polling-delay, bruk den vedvarende `Filtered Stream`-workeren:

```bash
./run-bot run-x-stream
```

Denne worker-en:

- kobler til `GET /2/tweets/search/stream`
- sørger for at stream-regelen finnes
- normaliserer hver innkommende post til `StructuredEvent`
- kjører `run_serverless_news_shock(...)` direkte når posten lander
- bruker samme Supabase-state, Telegram-varsler og dashboard som resten av systemet

Nye env-variabler:

- `X_BEARER_TOKEN`
- `X_CONSUMER_SECRET`
- `X_FILTERED_STREAM_RULE`
- `X_FILTERED_STREAM_RULE_TAG`
- `X_STREAM_ENABLED`
- `X_STREAM_CONNECT_TIMEOUT_SECONDS`
- `X_STREAM_READ_TIMEOUT_SECONDS`
- `X_STREAM_MAX_BACKOFF_SECONDS`
- `NEWS_SHOCK_REALTIME_WINDOW_SECONDS`

`X_CONSUMER_SECRET` beholdes for webhook/CRC-støtten, men den lav-latente veien er nå stream-worker, ikke webhook-linking.

Kilder brukt for implementasjonen og valg av løsning:

- [X Filtered Stream Introduction](https://docs.x.com/x-api/posts/filtered-stream/introduction)
- [X Manage Stream Rules](https://docs.x.com/x-api/posts/filtered-stream/quickstart/manage-rules)
- [X Webhooks Introduction](https://docs.x.com/x-api/webhooks/introduction)

## Vercel Cron

Prosjektet har nå Vercel-oppsett i [vercel.json](/Users/jesperhammersvik/Documents/Trading%20bot/vercel.json).

Endepunkter:

- `/api/cron/news-shock`
- `/api/cron/earnings-scan`
- `/api/cron/earnings-run`

Alle cron-endepunkter krever:

- `CRON_SECRET`

De forventer:

```text
Authorization: Bearer <CRON_SECRET>
```

Aktiv standard-cron i `vercel.json`:

- weekday earnings-scan på `/api/cron/earnings-scan`

Viktig per 5. april 2026:

- `Vercel Hobby` støtter bare svært begrenset cron-bruk, typisk rundt én kjøring per dag
- derfor er news trading på Vercel primært webhook-drevet
- hvis du vil kjøre `/api/cron/earnings-run` hvert 5. minutt, trenger du enten `Vercel Pro` eller en ekstern scheduler som kan kalle endpointet ditt med `CRON_SECRET`

Praktisk betyr det:

- `BTC` og `TSLA` news kan nå vurderes direkte når webhooken kommer
- daglig earnings-watchlist kan bygges på Vercel
- høyfrekvent earnings-polling er ikke realistisk på Hobby alene

## Multi-Source Ingestion

`news_shock` kan slå sammen flere kilder til ett corroborated event:

- `Finnhub webhook` via Vercel runtime-logs
- offisielle RSS-feeds, f.eks. `SEC` og `Fed`
- `White House` RSS og `EIA` petroleum-feed for geopolitikk/energi
- `SEC` company submissions for `TSLA`
- Alpaca-headlines som ekstra kontekst/korroborasjon

Hvis flere kilder beskriver samme sak innen kort tidsvindu, blir de samlet til ett `StructuredEvent` med:

- `supporting_sources`
- `source_count`
- `corroboration_score`

Strategien krever deretter:

- positivt surprise/sentiment/confidence
- pris- og volumbekreftelse
- minst `NEWS_SHOCK_MIN_SOURCE_COUNT` kilder

For `oil_policy` brukes en egen temaprofil:

- eventet må være Trump/White House-relatert og inneholde olje/energi-språk
- `trade_score` og `direction_score` styrer om proxyen får `BUY` eller `SHORT`
- short brukes bare på Alpaca-støttede aksjer/ETF-er, ikke på råolje-futures
- terskler styres av:
  - `NEWS_SHOCK_OIL_MIN_TRADE_SCORE`
  - `NEWS_SHOCK_OIL_MIN_CONFIDENCE`
  - `NEWS_SHOCK_OIL_CONFIRMATION_BARS`
  - `NEWS_SHOCK_OIL_VOLUME_MULTIPLIER`
  - `NEWS_SHOCK_OIL_RISK_PER_TRADE`

For `BTC/USD` bruker strategien en raskere profil i tillegg:

- lavere terskler for små, uventede positive nyheter
- færre confirmation bars
- mer tolerant volumfilter når volumfeltet er sparsomt
- exit når de siste barene faller og bevegelsen trekker nok tilbake fra topp
