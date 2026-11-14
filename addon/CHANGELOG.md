# Changelog

## 0.3.31

- fix: telegram audio transcript cause OOM kill

## 0.3.30

- Run decoupling judges steadiness on raw speed, not grade-adjusted speed. Grade adjustment moves speed about 5 % per 1 % of slope, so an evenly paced run on gentle rollers read as unsteady and its decoupling came back only as rough. Hills stay with the climbing check and the drift figure stays grade-adjusted; the cut-offs are unchanged.

## 0.3.29

- Run decoupling is now pace:HR, computed from the run's own streams. Intervals' decoupling is power:HR, and on a watch that estimates running power it measured a model. The new figure grade-adjusts speed with Minetti's energy-cost curve, trims the first 10 and last 5 minutes, drops stops and HR dropouts, and compares the first half with the second. It is read only on runs of 60+ minutes that were steady (pace varying ≤ 5 % minute to minute, ≤ 10 % as rough), not hilly (≤ 20 m climbing per km, ≤ 40 as rough), not at threshold (< 95 % of threshold pace), not stopped more than 10 % and not on trail. Heat over 25 °C is flagged. Treadmill runs count, without the hill check.
- `coach_load` adds `durability_running`: the same 7 vs 28 day durability as for rides, from readable runs only. It costs one streams request per long run, cached.
- `coach_sessions` no longer shows Intervals' power-based decoupling on runs; it points to `coach_activity`.

## 0.3.28

- Next week's run workouts in the weekly report are drawn in pace, like Intervals draws them: target pace over time (faster higher), labelled in min/km, coloured by pace zone, with the threshold pace as a dotted line. Pace zones, % of threshold pace, ramps and min/km targets are read; a step by distance takes its time from its own target pace; an HR-only step is drawn grey. Before, a run workout showed its duration and no targets.

## 0.3.27

- New read for runners, `coach_pace_curve`: best grade-adjusted times at 400 m, 1 km, mile, 5 km, 10 km, half and marathon over 42 days, 84 days and the year, with the recent speed as % of the year's best. It also returns Intervals' critical-speed fit (CS pace, D′, the efforts it rests on) and says whether it can be trusted: three or more efforts of 2–15 minutes, spread in duration, and a tight fit. A trusted CS is compared to the threshold pace in the run settings, so a stale threshold shows up.
- The weekly report's fifth chart follows the main sport of the last six weeks: the power profile for a cyclist, the new pace profile ("Curva di passo") for a runner.

## 0.3.26

- The coach reads each sport on its own terms. A run no longer carries the ride analysis (power block, W′, kJ above FTP, rolling FTP): it gets pace, GAP, % of threshold pace, a pace-based main set and its own aerobic signal, speed per beat (grade-adjusted m/min per bpm). Watch-estimated running power is still there, as a second view only. A ride is unchanged.
- Runs no longer leak into ride metrics: decoupling on a run is never judged by the variability index of estimated power, and durability counts rides only.
- `coach_sessions` rows show ride columns on rides and run columns (GAP, % threshold pace, speed per beat) on runs. `coach_history` adds `run_speed_hr_easy_mean`, the weekly run aerobic trend on easy runs of 30+ minutes.
- Weekly charts: the daily load chart now shows running load (it was missing), and the trend chart stacks bike and run hours ("Andamento settimanale: eFTP e ore per sport").

## 0.3.25

- The coach reads a run's reps on pace again when the watch records power. A Forerunner estimates power on every run, so `coach_activity` judged every structured run like a ride (watts, % of FTP) and returned no pace per rep. Runs now always get the pace-based `main_set` (pace per rep, GAP, % of threshold, fade, CV), with the same reps in watts alongside in `main_set_power`. Run zones, and the training split built on them, now use pace or GAP zones first and power only when no pace zones came through.

## 0.3.24

- Update Section11

## 0.3.23

- Chat turns no longer run out of time on a session that grew for weeks. The chat started from one Claude session and resumed it across days and restarts, until every call re-read about 540k tokens and a turn with a few tool calls passed the 5-minute limit ("That took too long and I cut it off"). Now each day starts a fresh session (the day turns at 04:00), and so does a session whose context passes 150k tokens (SESSION_MAX_TOKENS in .env on a self-hosted install). The first turn of every new session carries the last seven days' digests and today's messages, and memory_search reaches everything older. The first message after updating starts fresh.

## 0.3.22

- The assistant now starts each day knowing what has been going on. Every night, after distilling, it writes a short summary of each past day into memory/digest/; the last seven, plus what you have said today, are given to it on the first message of each day and on every routine, so it no longer depends on claude-mem for that. The first night after updating writes summaries for every day already in the log (one model call per day, once). Also fixed: the nightly distill ran at 03:30 against the new, empty day, so yesterday's conversation was never distilled into the wiki; before noon it now reads yesterday's log.

## 0.3.21

- New memory_search tool: the assistant searches everything you have said to it and the wiki, by exact words and by meaning, in Italian and English, with dates ("ieri", "21 agosto", "last week"). It runs inside the add-on with a small local model (about 130 MB, downloaded once into /data on first use); nothing leaves the box, and it works on OpenRouter too. On a test of 52 questions written from real conversations, the assistant found the right exchange or wiki page for 96% of them. claude-mem stays installed alongside it for now. On a self-hosted install, RECALL_EMBED=0 in .env matches words only and skips the model.

## 0.3.20

- claude-mem remembers again. Its observer — the part that turns each session into memory — signs in on its own and never saw the add-on's Claude credential, so every observation failed with "could not authenticate the observer" and nothing new was remembered. Each start now hands it the credential: a subscription token in ~/.claude/.credentials.json, an API key in ~/.claude-mem/.env. claude-mem is pinned to 15.9.0 so an image rebuild no longer changes how it signs in. /selftest now fails the claude-mem line when the observer is failing, instead of reporting the worker ready.

## 0.3.19

- Cheaper model calls on OpenRouter. The system prompt no longer carries the clock, which changed it every minute and stopped any provider from caching it; the time now goes in front of each of your messages. Anthropic models get cache breakpoints, so each tool step reads the conversation from cache. Long chats are trimmed once, from 200k down to 100k characters, rather than a little on every message.
- The log records what each turn cost: tokens in, how many came from cache, tokens out, and the price, on both the Claude and OpenRouter backends.

## 0.3.18

- Routines run in a fresh session of their own, not inside the chat. Before, a routine saw whatever was still in the chat's context: the weekly coach report run twice within half an hour skipped filing its report as a "duplicate", and the mail went out as plain text without next week's sessions. The chat session is untouched by a routine run, so the chat no longer sees the routine's output either; the wiki and the log still record it.

## 0.3.17

- `/selftest` checks every connector in one go — model, Google sign-in, Calendar, Gmail, each Drive folder, Intervals.icu, scraper, mail route, voice, claude-mem, routines, skills — and answers one line each with the reason when something fails. Reads only; addresses masked. `/selftest mail` sends one real test mail to Mail to and shows the error instead of falling back to Telegram. Every start logs a one-line `selftest:` summary.

## 0.3.16

- Mail: with SMTP host and SMTP from set, routines with channel email now go out through SMTP to Mail to, as the form says. Before, SMTP was used only if the legacy SMTP to was also set; otherwise the add-on tried Gmail through gws, and without a Google sign-in every mail fell back to Telegram.

## 0.3.15

- Sign in to Google from the chat: put your Desktop OAuth client id and secret in the new Google options, send /google, paste back the address the page lands on. No terminal, no docker exec. Publish the Cloud app (In production) or Google ends the sign-in after seven days. Installs logged in the old way keep working.

## 0.3.14

- The options form names and explains every field. The three old credential fields show as "Legacy" and say to leave them empty in favour of Model provider + Model credential.
- Upgrading from 0.3.5 or earlier with a Telegram id saved: the form refuses to save with "expected str" until the id is text. Use ⋮ > Edit in YAML and quote it: `- "123456789"`.
- `scripts/snap-instance.sh` commits and pushes the private instance repo (`context/`, `memory/wiki/`, `routines/`) in one step; `SNAP_NO_PUSH=1` commits only.

## 0.3.13

- Coach: next week's calendar can be read again. Asking for a week that had not started yet failed with HTTP 422, so the coach could not see or plan the coming week.

## 0.3.12

- Coach: confirming a workout you already saw in a preview no longer gets refused for not re-reading the coaching protocol first, which cost a round-trip on every "yes".
- Coach: when Intervals.icu rejects a write (e.g. HTTP 422), the reply now says what it rejected instead of just the status code.

## 0.3.11

- Docs: the install is now five steps (three options, start, pair, answer), and the Commands section lists /onboard, /proposals, /review, /accept and /reject. owner_name and owner_language are documented.

## 0.3.10

- The first conversation starts itself: right after pairing the bot interviews you one topic at a time, files the lists, and turns brief answers into proposals for /review and /accept. /onboard starts it again, /onboard <topic> covers one; /help says how many TO FILL lines are still open. The laptop wizard's --addon mode is gone: nothing to mount, nothing to paste.

## 0.3.9

- Briefs change from the chat: the assistant proposes a whole new brief, you read the diff with /review <brief> and apply it with /accept (/proposals lists, /reject discards). The coach's intake files its stable answers this way instead of asking you to copy lines by hand. Edits to context/ on the share now apply within seconds, no restart.

## 0.3.8

- Pair instead of looking up your Telegram id: leave telegram_allowed_user_ids empty, find `PAIRING CODE` in the Log tab, send it to the bot. That chat becomes the owner and pairing closes. Ids in the options still work and replace a paired owner.

## 0.3.7

- One model credential instead of three: pick llm_provider (claude_subscription, anthropic_api or openrouter) and paste it in llm_credential. A credential that belongs to another provider fails at start, and the Log names the right provider. The old claude_oauth_token / anthropic_api_key / openrouter_api_key fields keep working while llm_credential is empty.

## 0.3.6

- web_fetch_allowed_domains is optional again, and `*` in it means the open web instead of blocking every site. telegram_allowed_user_ids takes ids as text, so the options form accepts them.

## 0.3.5

- Wiki pages can no longer be overwritten whole: Write only creates a page, changes go through Edit, and memory/wiki/log.md only grows. Protects against models that rewrite a file to add one line.

## 0.3.4

- Coach covers running as well as cycling: run pace, grade-adjusted pace and pace zones, per-sport load, run thresholds, a running workout library and multi-sport planning. Existing installs: compare your context/training-rules.md with the new default.

## 0.3.3

- Coach: Section 11 protocol updated from 11.77 to 11.100 (reworked report templates, separate plan match and step compliance).

## 0.3.2

- One-shot import of an existing instance: memory/wiki, memory/raw and routines dropped under `import/` in the add-on config folder are moved into `/data/paia` on start, then `import/` is deleted.

## 0.3.1

- OpenRouter options shown in the Configuration tab without the "unused optional" toggle.
- `coach` added to the default skills. Existing installs: add it to `skills` by hand.

## 0.3.0

- Run the assistant on any tool-calling model through OpenRouter (`openrouter_api_key`).
