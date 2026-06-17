import logging
import numpy as np
from src.database import get_connection


def store_embedding(name: str, category: str, embedding: np.ndarray,
                    embed_model: str, source_file: str, config: dict):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO gallery (name, category, embedding, embed_model, source_file)
                   VALUES (%s, %s, %s, %s, %s)""",
                (name, category, embedding.astype(np.float32).tobytes(), embed_model, source_file)
            )
        logging.info(f"Enrolled {name}/{category} ({embed_model})")
    finally:
        conn.close()


def load_gallery(category: str, embed_model: str, config: dict) -> dict:
    """Return {name: mean_unit_embedding} for the given category and model."""
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT name, embedding FROM gallery WHERE category = %s AND embed_model = %s",
                (category, embed_model)
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    by_name: dict = {}
    for name, blob in rows:
        emb = np.frombuffer(blob, dtype=np.float32).copy()
        by_name.setdefault(name, []).append(emb)

    result = {}
    for name, embeddings in by_name.items():
        mean_emb = np.mean(embeddings, axis=0)
        result[name] = mean_emb / (np.linalg.norm(mean_emb) + 1e-8)
    return result


def match(query_embedding: np.ndarray, gallery: dict, threshold: float):
    """
    Cosine similarity against {name: unit_embedding}.
    Returns (name, similarity) if above threshold, else (None, None).
    """
    if not gallery:
        return None, None
    q = query_embedding.astype(np.float32)
    q = q / (np.linalg.norm(q) + 1e-8)
    best_name, best_sim = None, 0.0
    for name, emb in gallery.items():
        sim = float(np.dot(q, emb))
        if sim > best_sim:
            best_sim, best_name = sim, name
    if best_sim >= threshold:
        return best_name, round(best_sim, 3)
    return None, None


def list_enrolled(config: dict) -> list:
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT name, category, COUNT(*) AS count,
                          MAX(enrolled_at) AS last_enrolled, embed_model
                   FROM gallery
                   GROUP BY name, category, embed_model
                   ORDER BY category, name"""
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        {"name": r[0], "category": r[1], "count": r[2],
         "last_enrolled": str(r[3]), "embed_model": r[4]}
        for r in rows
    ]


def remove_enrolled(name: str, category: str, config: dict) -> int:
    """Delete all gallery entries for name+category. Returns rows deleted."""
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM gallery WHERE name = %s AND category = %s",
                (name, category)
            )
            count = cursor.rowcount
    finally:
        conn.close()
    logging.info(f"Removed {count} embeddings for {name}/{category}")
    return count
