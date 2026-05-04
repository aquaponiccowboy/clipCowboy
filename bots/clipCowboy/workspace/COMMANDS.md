# COMMANDS.md

These are your executable commands for the ClipCowboy video pipeline.
Read this file on every startup. Follow instructions literally.

---

## factory_reset

**What it does:** Clears the pipeline's processed-file history in MariaDB so
test clips can be re-run through the full pipeline. This operates on a VIDEO
ANALYTICS DATABASE — it does NOT reset your memory, session, or conversation.

**Trigger phrases:** "factory reset", "reset pipeline", "clear history",
"start fresh", "wipe the database"

**Step 1 — attempt HTTP call:**
```
POST http://192.168.1.38:8765/reset
Content-Type: application/json

{"scores": true, "minio": false}
```

If the user says "full reset" or "wipe everything" or mentions MinIO/buckets, use:
```json
{"scores": true, "minio": true}
```

**Step 2 — if HTTP call succeeds:** Report what was cleared.
Example: "Reset complete. Cleared 142 processed_files, 38 dlq_files, 142 clip_scores."

**Step 3 — if PIPELINE_CONTROL_URL is not set or call fails:** Tell the user:
> Control server unreachable. Run this on the host:
> ```
> python3 reset_pipeline.py --confirm --scores
> ```
> Add `--minio` to also clear MinIO buckets.

---

## pipeline_status

**What it does:** Shows current row counts across all pipeline tables.

**Trigger phrases:** "pipeline status", "how many clips", "what's in the database"

**HTTP call:**
```
GET http://192.168.1.38:8765/status
```

**If unreachable:** Tell the user to run `python3 pipeline_status.py`

---

## Notes

- "gallery" = enrolled recognition data (persons, pets, objects). NEVER clear it.
- If a command isn't listed here, say "not implemented."
- Do not invent behaviour beyond what's defined above.
