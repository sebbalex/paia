# Changelog

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
