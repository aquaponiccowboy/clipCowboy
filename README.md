# SecurityCowboy

RabbitMQ-decoupled dashcam footage pipeline. Ingests `.TS` files from a 4-camera truck system, transcodes to MP4, runs YOLO object detection, and sorts results into content-based bins. Long-term goal: process ~2 TB of tiny-home build footage for content editing — person/object re-identification, scene detection, highlight reels.

---

## Prerequisites

- Docker Desktop (runs MariaDB, MinIO, RabbitMQ)
- Python 3.10+
- NVIDIA GPU + CUDA (YOLO will fall back to CPU if unavailable)
- ffmpeg on PATH

---

## First-Time Setup

### 1. Start infrastructure

```bash
docker-compose up -d
```

### 2. Configure

```bash
cp config.yml.example config.yml
```

Edit `config.yml` — fill in credentials, set your model path, tune the spatial masks per camera.  
Edit `.env` if you changed credentials in `docker-compose.yml`.

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Create buckets, verify DB schema, confirm RabbitMQ

```bash
python3 setup.py
```

---

## Running the Pipeline

### Start all workers (one terminal)

```bash
python3 run_pipeline.py
```

This starts three workers:
- **watcher** — scans the `input` MinIO bucket every 60 s, publishes new `.TS` files to the transcode queue
- **worker_transcode** — converts `.TS` → `.MP4`, hands off to the analyze queue
- **worker_analyze** — runs YOLO, routes clips to `archive` (detections found) or `quarantine` (nothing found)

### Watch pipeline status (separate terminal)

```bash
python3 pipeline_status.py        # refreshes every 5 s
python3 pipeline_status.py --once # single snapshot
```

---

## Bucket Layout

| Bucket | What lives here |
|---|---|
| `input` | Raw `.TS` files from the dashcam — drop footage here |
| `converted` | In-flight `.MP4` only; always cleaned up after processing |
| `archive` | Raw `.MP4` clips where YOLO found something — original filename, never renamed |
| `annotated` | `.MP4` with bounding boxes drawn — only written when `save_annotated: true` |
| `quarantine` | No-detection clips, kept for spot-check before deletion |

After `sort_archive.py` runs, clips inside `archive` are organised into sub-folders:

```
archive/
  people/
  vehicles/
  animals/
  people+vehicles/
  other/            ← detections found, but no configured category matched
```

---

## Overnight Batch Workflow

```bash
# 1. Drop .TS files into the input bucket (MinIO browser at http://localhost:9001)

# 2. Start the pipeline and let it run
python3 run_pipeline.py

# 3. (Next morning) Sort archived clips into content bins
python3 sort_archive.py --dry-run   # preview counts first
python3 sort_archive.py             # execute

# 4. Score clips by activity level
python3 score_clips.py              # score all unscored clips
python3 score_clips.py --top 20     # score and immediately show top 20

# 5. Browse results in MinIO browser, or query from the CLI
python3 query_detections.py --category people
python3 query_detections.py --category vehicles --camera F
python3 query_detections.py --object person --min-confidence 0.90

# 6. Clean up quarantine when you're satisfied nothing was missed
python3 purge_quarantine.py --dry-run
python3 purge_quarantine.py --yes
```

---

## CLI Tools Reference

### `sort_archive.py` — organise clips into content bins

```bash
python3 sort_archive.py --dry-run          # preview without moving anything
python3 sort_archive.py                    # execute sort
python3 sort_archive.py --resort           # re-sort everything after changing categories
```

Categories are defined in `config.yml` under `sort.categories`. Each maps a name to a list of YOLO labels. Clips matching multiple categories get a combined bin (`people+vehicles`). Clips with detections that match no category go to `other`.

---

### `score_clips.py` — rank clips by activity level

Scores each archived clip 0–1 using the YOLO events already in the database. No video files are read. Weights are tunable in `config.yml` under `scoring.weights`.

```bash
python3 score_clips.py                    # score all unscored archived clips
python3 score_clips.py --rescore          # rescore after tuning weights
python3 score_clips.py --top 20           # score then show top 20
python3 score_clips.py --list --top 20    # show top 20 without scoring
python3 score_clips.py --list --min-score 0.7
python3 score_clips.py --camera F --top 10
```

Score components (default weights in parentheses):

| Component | Weight | What it measures |
|---|---|---|
| `detection_rate` | 0.35 | Fraction of clip seconds with at least one detection |
| `avg_objects` | 0.25 | Mean simultaneous detections per second |
| `label_diversity` | 0.20 | Distinct YOLO labels seen in the clip |
| `mean_confidence` | 0.10 | Average detection confidence |
| `temporal_coverage` | 0.10 | How evenly spread detections are across the clip |

---

### `query_detections.py` — search footage by what was detected

```bash
# See every object label YOLO has produced
python3 query_detections.py --list-objects

# Find clips by object type
python3 query_detections.py --object person
python3 query_detections.py --object car --camera F

# Filter by confidence, category, or date
python3 query_detections.py --object person --min-confidence 0.90
python3 query_detections.py --category people+vehicles
python3 query_detections.py --after 2026-03-01 --before 2026-03-31
```

---

### `reprocess.py` — send archived clips back through the pipeline

Use this when upgrading the YOLO model or changing detection settings. Copies clips from `archive` back to `converted`; the watcher's recovery path re-queues them automatically.

```bash
python3 reprocess.py --dry-run                      # preview
python3 reprocess.py --category people              # reprocess a bin
python3 reprocess.py --camera L                     # reprocess one camera
python3 reprocess.py --glob "00011624_*"            # reprocess by filename
python3 reprocess.py --after 2026-03-01 --before 2026-03-31
```

Previous detection records for the old model version are preserved. The new model's results are appended as a separate row.

---

### `purge_quarantine.py` — reclaim SAN space

```bash
python3 purge_quarantine.py --dry-run               # shows file count and total size
python3 purge_quarantine.py --yes                   # skip confirmation prompt
python3 purge_quarantine.py --camera F --dry-run    # front camera only
python3 purge_quarantine.py --before 2026-01-01 --yes
```

---

## Named Recognition (Step 2)

Named recognition identifies known individuals and routes clips into per-person bins within each category:

```
archive/
  people/
    Zeke/           ← recognized clips
    Zeke+Neighbor/  ← clips with both recognized
    (root)          ← unrecognized persons
  vehicles/
    BlueTruck/
```

Recognition runs during analysis when `recognition.enabled: true` in `config.yml`. Set `recognition.threshold` to tune sensitivity (cosine similarity, 0–1).

### `enroll.py` — register known individuals

Point it at any reference video or image. YOLO finds the detections, the embedding model encodes them, and they're stored in the gallery DB table.

```bash
# Enroll from a video clip (samples up to 30 frames)
python3 enroll.py --name Zeke --category people --from reference.mp4

# Enroll from a still image
python3 enroll.py --name BlueTruck --category vehicles --from truck.jpg

# Limit frame sampling
python3 enroll.py --name Zeke --category people --from clip.mp4 --max-frames 60

# See who's enrolled and how many samples each has
python3 enroll.py --list

# Remove someone from the gallery
python3 enroll.py --remove --name Zeke --category people
```

After enrolling, set `recognition.enabled: true` in `config.yml` and restart workers. Re-run `sort_archive.py --resort` to re-sort already-processed clips with the new names.

### Recognition models

| Category | Model | Notes |
|---|---|---|
| `people` | InsightFace buffalo_l (ArcFace) | Face-based; returns `None` if no face visible |
| `vehicles` | OpenCLIP ViT-B-32 | Visual appearance embedding |
| `animals` | OpenCLIP ViT-B-32 | Visual appearance embedding |

Install recognition dependencies (not included in base requirements):

```bash
pip install insightface onnxruntime-gpu open-clip-torch
```

---

## Camera IDs

Four cameras identified by the last character before the file extension:

| ID | Position |
|---|---|
| `F` | Front |
| `B` | Back |
| `L` | Left |
| `R` | Right |

Resolution: 640×480. Spatial masks per camera are configured in `config.yml` under `filters`.

---

## Dead Letter Queue

Workers retry failed jobs up to 3 times. On the third failure, the job is routed to a dead letter queue (`footage.transcode.dlq` or `footage.analyze.dlq`) and a record is written to the `dlq_files` DB table. The watcher will not re-queue files already in the DLQ.

To inspect DLQ'd files:

```sql
SELECT file_key, queue, error, failed_at FROM dlq_files;
```

To remove a file from the DLQ and allow reprocessing, delete its row from `dlq_files`, then run `reprocess.py`.

---

## Roadmap

- **Scene / situation detection** — temporal models for activity recognition
- **Content editing pipeline** — highlight reel generation, cross-clip search by person
- **OpenClaw / Discord integration** — bot commands for pipeline status, query, and alerts
