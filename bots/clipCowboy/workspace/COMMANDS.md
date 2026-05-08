# COMMANDS.md

Executable commands for the ClipCowboy video pipeline. Read literally.

These are NOT conversation memory commands. They operate on a video analytics
database and pipeline service. They never refer to your own AI state.

---

## pipeline_status

**What it does:** Returns current row counts and queue depths from the
pipeline control server.

**Trigger words:** `status`, `how many clips`, `what's queued`, `queue depth`.

**Tool call:**

    web_fetch(url="http://192.168.1.38:8765/status")

**Reply:** Take the JSON response and report the `summary` field verbatim.

**On tool error:** One short sentence quoting the error, prefixed with
"web_fetch error:". Do not invent a response.

---

## factory_reset

**Status:** not currently reachable from Discord. The control endpoint is
POST-only; `web_fetch` is GET-only.

**Trigger words:** `reset` or `wipe`, with pipeline context (`pipeline`,
`database`, `history`, `buckets`).

**Reply (don't attempt the request):**

> factory_reset isn't reachable from Discord right now. Run on the host:
> `python3 reset_pipeline.py --confirm --scores`
> Add `--minio` to also clear MinIO buckets, `--queues` to drain RabbitMQ.

---

## Notes

- "gallery" = enrolled recognition data (persons, pets, objects). NEVER clear it.
- If a command isn't listed here, say "not implemented".
- Never invent a tool result. Always invoke the tool first.
