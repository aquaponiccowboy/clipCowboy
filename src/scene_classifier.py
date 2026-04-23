"""
Zero-shot video scene classification using OpenCLIP.

Samples N frames from a local video file, averages their CLIP image embeddings,
then compares against configurable text prompt sets to produce per-scene scores.

No training data required. Text features are cached after first computation
so repeated calls across many clips only pay the encoding cost once.

Model: same ViT-B-32/openai already pulled in by src/recognizer.py.
"""
import logging
import numpy as np
import cv2

_clip_model = None
_clip_preprocess = None
_text_cache: dict = {}   # scene_name → unit text feature tensor

_MODEL_NAME  = 'ViT-B-32'
_PRETRAINED  = 'openai'
EMBED_MODEL  = f'open_clip/{_MODEL_NAME}'


def _get_clip():
    global _clip_model, _clip_preprocess
    if _clip_model is None:
        import open_clip
        _clip_model, _, _clip_preprocess = open_clip.create_model_and_transforms(
            _MODEL_NAME, pretrained=_PRETRAINED
        )
        _clip_model.eval()
        logging.info(f"OpenCLIP loaded ({_MODEL_NAME}/{_PRETRAINED}) for scene classification.")
    return _clip_model, _clip_preprocess


def _encode_text(model, prompts: list):
    import torch, open_clip
    tokenizer = open_clip.get_tokenizer(_MODEL_NAME)
    tokens = tokenizer(prompts)
    with torch.no_grad():
        feats = model.encode_text(tokens)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats  # (n_prompts, 512)


def _get_text_features(model, scene_name: str, prompts: list):
    if scene_name not in _text_cache:
        _text_cache[scene_name] = _encode_text(model, prompts)
    return _text_cache[scene_name]


def _sample_frames(video_path: str, n: int) -> list:
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or n
    indices = sorted({int(i * total / n) for i in range(n)})
    frames = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret:
            frames.append(frame)
    cap.release()
    return frames


def classify(video_path: str, prompts_cfg: dict, n_frames: int = 8) -> dict:
    """
    Classify a video clip against the configured scene prompts.

    prompts_cfg: {scene_name: [prompt, prompt, ...]}
    Returns {scene_name: score_0_to_1} for all scenes, sorted by score desc.
    Also returns a special '__top__' key with the winning scene name.
    """
    import torch
    from PIL import Image

    frames = _sample_frames(video_path, n_frames)
    if not frames:
        return {}

    model, preprocess = _get_clip()

    # Encode frames → average image embedding
    image_tensors = []
    for bgr in frames:
        rgb = bgr[:, :, ::-1].astype(np.uint8)
        pil = Image.fromarray(rgb)
        image_tensors.append(preprocess(pil))

    import torch
    batch = torch.stack(image_tensors)
    with torch.no_grad():
        img_feats = model.encode_image(batch)
        img_feats = img_feats / img_feats.norm(dim=-1, keepdim=True)
    mean_feat = img_feats.mean(dim=0, keepdim=True)
    mean_feat = mean_feat / mean_feat.norm(dim=-1, keepdim=True)

    # Score against each scene's prompts; take max similarity across prompts
    scores = {}
    for scene_name, prompts in prompts_cfg.items():
        text_feats = _get_text_features(model, scene_name, prompts)
        sims = (mean_feat @ text_feats.T).squeeze(0)
        scores[scene_name] = round(float(sims.max().item()), 4)

    # Normalize to 0–1 relative to the observed range so scores are comparable
    if scores:
        lo, hi = min(scores.values()), max(scores.values())
        span = hi - lo if hi > lo else 1.0
        scores = {k: round((v - lo) / span, 4) for k, v in scores.items()}

    top_scene = max(scores, key=scores.get) if scores else None
    return {'__top__': top_scene, **dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))}
