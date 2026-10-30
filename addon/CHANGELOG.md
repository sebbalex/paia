# Changelog

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
