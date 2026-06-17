"""
Lazy-loading embedding extractors for named recognition.

- InsightFace (buffalo_l ArcFace) for persons  → 512-dim float32
- OpenCLIP (ViT-B-32/openai) for other categories → 512-dim float32

Both return unit vectors. Imports are deferred so the workers start even
when insightface / open_clip are not yet installed.
"""
import logging
import numpy as np

_face_app = None
_clip_model = None
_clip_preprocess = None

_CLIP_MODEL = 'ViT-B-32'
_CLIP_PRETRAINED = 'openai'


def _get_face_app():
    global _face_app
    if _face_app is None:
        from insightface.app import FaceAnalysis
        _face_app = FaceAnalysis(
            name='buffalo_l',
            providers=['CUDAExecutionProvider', 'CPUExecutionProvider'],
        )
        _face_app.prepare(ctx_id=0, det_size=(320, 320))
        logging.info("InsightFace loaded (buffalo_l).")
    return _face_app


def _get_clip():
    global _clip_model, _clip_preprocess
    if _clip_model is None:
        import open_clip
        _clip_model, _, _clip_preprocess = open_clip.create_model_and_transforms(
            _CLIP_MODEL, pretrained=_CLIP_PRETRAINED
        )
        _clip_model.eval()
        logging.info(f"OpenCLIP loaded ({_CLIP_MODEL}/{_CLIP_PRETRAINED}).")
    return _clip_model, _clip_preprocess


def _crop(frame_bgr: np.ndarray, xyxy: list, pad: int = 20) -> np.ndarray:
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in xyxy)
    return frame_bgr[max(0, y1 - pad):min(h, y2 + pad),
                     max(0, x1 - pad):min(w, x2 + pad)]


def get_embedding(frame_bgr: np.ndarray, xyxy: list, category: str, rec_cfg: dict):
    """
    Extract a 512-dim unit embedding for a detected object crop.

    rec_cfg: config['recognition']['categories'][category]
    Returns (embedding, model_name) or (None, None) on failure.
    """
    crop = _crop(frame_bgr, xyxy)
    if crop.size == 0:
        return None, None

    model_type = rec_cfg.get('model', 'clip')

    if model_type == 'insightface':
        try:
            app = _get_face_app()
            faces = app.get(crop)
            if not faces:
                return None, None
            face = max(faces, key=lambda f: f.det_score)
            return face.normed_embedding.astype(np.float32), 'insightface/buffalo_l'
        except Exception as e:
            logging.debug(f"InsightFace crop failed: {e}")
            return None, None

    else:  # clip
        try:
            import torch
            from PIL import Image
            model, preprocess = _get_clip()
            pil = Image.fromarray(crop[:, :, ::-1].astype(np.uint8))
            tensor = preprocess(pil).unsqueeze(0)
            with torch.no_grad():
                features = model.encode_image(tensor)
                features = features / features.norm(dim=-1, keepdim=True)
            return features[0].cpu().numpy().astype(np.float32), f'open_clip/{_CLIP_MODEL}'
        except Exception as e:
            logging.debug(f"CLIP crop failed: {e}")
            return None, None
