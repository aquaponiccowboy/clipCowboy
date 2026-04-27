# SecurityCowboy

4-camera dashcam pipeline: `.TS` → transcode → YOLO → sort → score. Built for processing ~2 TB of tiny-home build footage.

**Stack:** RabbitMQ · MinIO · MariaDB · YOLOv8 · OpenClaw · Docker

---

## Setup

```bash
docker-compose up -d                  # start MariaDB, MinIO, RabbitMQ, bot
cp config.yml.example config.yml      # fill in credentials, model path, masks
cp .env.example .env                  # fill in DB/MinIO/Discord/OpenClaw tokens
pip install -r requirements.txt
python3 setup.py                      # create buckets, verify DB schema
```

---

## Run

```bash
python3 run_pipeline.py               # watcher + transcode + analyze workers
python3 pipeline_status.py            # live status, refreshes every 5s
python3 pipeline_status.py --once     # single snapshot
```

---

## Overnight Workflow

```bash
# drop .TS files into MinIO input bucket (http://localhost:9001)
python3 run_pipeline.py

# next morning:
python3 sort_archive.py --dry-run     # preview sort
python3 sort_archive.py               # move clips into category bins
python3 score_clips.py --top 20       # score by activity, show busiest 20
python3 extract_highlights.py --min-score 0.4  # cut sub-clips from active windows
python3 classify_scenes.py --min-score 0.4     # label scenes with CLIP
python3 query_detections.py --category people --camera F
python3 purge_quarantine.py --dry-run # review no-detection clips
python3 purge_quarantine.py --yes     # delete them
```

---

## Buckets

| Bucket | Contents |
|---|---|
| `input` | Raw `.TS` files — drop footage here |
| `converted` | In-flight `.MP4` — always cleaned up |
| `archive` | `.MP4` clips with detections |
| `annotated` | Bounding-box `.MP4` — only when `save_annotated: true` |
| `quarantine` | No-detection clips — spot-check then purge |
| `highlights` | Trimmed sub-clips from `extract_highlights.py` |

After `sort_archive.py`:
```
archive/
  people/Zeke/        ← named (recognition enabled)
  people/             ← unrecognized persons
  vehicles/
  animals/
  people+vehicles/
  other/
```

---

## CLI Reference

### `sort_archive.py`
```bash
python3 sort_archive.py               # sort unsorted clips into category bins
python3 sort_archive.py --dry-run     # preview only
python3 sort_archive.py --resort      # re-sort after changing categories in config
```

### `score_clips.py`
```bash
python3 score_clips.py                # score all unscored clips (0–1)
python3 score_clips.py --rescore      # rescore after tuning weights in config
python3 score_clips.py --top 20       # score then show busiest 20
python3 score_clips.py --list --top 20           # show without rescoring
python3 score_clips.py --list --min-score 0.7
python3 score_clips.py --camera F --top 10
```
Score weights tunable in `config.yml` under `scoring.weights` (detection_rate · avg_objects · label_diversity · mean_confidence · temporal_coverage).

### `query_detections.py`
```bash
python3 query_detections.py --list-objects                    # all YOLO labels seen
python3 query_detections.py --object person
python3 query_detections.py --object car --camera F
python3 query_detections.py --object person --min-confidence 0.90
python3 query_detections.py --category people+vehicles
python3 query_detections.py --after 2026-03-01 --before 2026-03-31
```

### `reprocess.py`
```bash
# copies clips back to converted so the analyze worker re-runs them
python3 reprocess.py --dry-run
python3 reprocess.py --category people
python3 reprocess.py --camera L
python3 reprocess.py --glob "00011624_*"
python3 reprocess.py --after 2026-03-01 --before 2026-03-31
```

### `classify_scenes.py`
```bash
python3 classify_scenes.py                      # all unclassified archived clips
python3 classify_scenes.py --min-score 0.3      # only above heuristic score threshold
python3 classify_scenes.py --top 50             # top 50 by heuristic score
python3 classify_scenes.py --camera F
python3 classify_scenes.py --reclassify         # redo after editing prompts in config
python3 classify_scenes.py --dry-run            # list clips without downloading
python3 classify_scenes.py --list               # show stored labels
python3 classify_scenes.py --list --scene construction_work
```
Prompts defined in `config.yml` under `scenes.prompts` — add/rename scenes freely, no retraining needed.

### `extract_highlights.py`
```bash
python3 extract_highlights.py                   # all scored clips
python3 extract_highlights.py --min-score 0.5   # skip low-activity clips
python3 extract_highlights.py --top 50          # top 50 by score only
python3 extract_highlights.py --camera F
python3 extract_highlights.py --dry-run         # show windows, no ffmpeg
python3 extract_highlights.py --re-extract      # redo already-extracted
python3 extract_highlights.py --list            # show extracted highlights
python3 extract_highlights.py --list --top 20 --min-score 0.4
```
Window parameters tunable in `config.yml` under `highlights` (min_duration · max_duration · merge_gap · pad · min_clip_score).

### `purge_quarantine.py`
```bash
python3 purge_quarantine.py --dry-run              # file count + total size
python3 purge_quarantine.py --yes
python3 purge_quarantine.py --camera F --dry-run
python3 purge_quarantine.py --before 2026-01-01 --yes
```

### `enroll.py`
```bash
# register known individuals for named recognition
python3 enroll.py --name Zeke --category people --from reference.mp4
python3 enroll.py --name Zeke --category people --from photo.jpg --max-frames 60
python3 enroll.py --list
python3 enroll.py --remove --name Zeke --category people
```
After enrolling: set `recognition.enabled: true` in config, restart workers, then `sort_archive.py --resort`.

Recognition deps (not in base requirements):
```bash
pip install insightface onnxruntime-gpu open-clip-torch
```

---

## Cameras

| ID | Position |
|---|---|
| `F` | Front |
| `B` | Back |
| `L` | Left |
| `R` | Right |

640×480. Spatial masks configured in `config.yml` under `filters`.

---

## Dead Letter Queue

Workers retry 3× then route to DLQ. File is not re-queued by watcher.

```sql
SELECT file_key, queue, error, failed_at FROM dlq_files;
-- fix the cause, then:
DELETE FROM dlq_files WHERE file_key = 'problem_file.mp4';
-- then run reprocess.py
```

---

## Bot (Discord)

OpenClaw runs as the `bot` service in docker-compose. Bot scripts live in `bot/` and are mounted as `/workspace` inside the container.

```bash
docker compose logs -f bot            # watch bot logs
docker compose restart bot            # reload after config changes
```

Bot commands are defined in `bot/` — see that directory for available commands once connected.

---

## Roadmap

- Discord bot commands: pipeline status, score queries, scene search, highlight delivery
