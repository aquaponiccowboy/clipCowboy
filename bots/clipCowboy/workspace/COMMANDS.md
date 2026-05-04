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

**Step 2 — if HTTP 202:** Confirm accepted.
Example: "Reset accepted — running in background. The pipeline will post the summary when it finishes."

**Step 3 — if call fails:** Report the error.
Example: "Reset failed — connection refused at http://192.168.1.38:8765. Is the pipeline running?"

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
