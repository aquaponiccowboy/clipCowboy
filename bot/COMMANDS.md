# COMMANDS.md — Available Pipeline Commands

When a user asks you to run a pipeline command, execute it using the HTTP
control server at the URL in `PIPELINE_CONTROL_URL`. Respond with a concise
summary of what was cleared. If `PIPELINE_CONTROL_URL` is not set, tell the
user to run the command manually on the host.

---

## factory_reset

Wipe processed-file history so test clips run through the full pipeline again.
Gallery (enrolled persons/pets/objects) is always preserved.

**Trigger phrases:** "factory reset", "reset pipeline", "clear history", "start fresh"

**HTTP call:**
```
POST {PIPELINE_CONTROL_URL}/reset
Content-Type: application/json

{"scores": false, "minio": false}
```

**Options (include in JSON body):**
- `"scores": true` — also clear clip_scores, highlights, scene_labels
- `"minio": true` — also delete objects in converted + processed MinIO buckets (forces re-transcode)

**Example responses to user:**

> Reset complete. Cleared 142 processed_files, 38 dlq_files. Gallery untouched (12 enrolled).

> Reset complete. Cleared 142 processed_files, 38 dlq_files, 142 clip_scores, 89 highlights, 142 scene_labels. Gallery untouched.

---

## pipeline_status

Check current row counts without changing anything.

**HTTP call:**
```
GET {PIPELINE_CONTROL_URL}/status
```

Returns counts for all tables. Format as a short summary.

---

## Manual fallback (if control server unreachable)

Tell the user to run on the host:
```bash
python3 reset_pipeline.py --confirm
python3 reset_pipeline.py --confirm --scores
python3 reset_pipeline.py --confirm --scores --minio
```
