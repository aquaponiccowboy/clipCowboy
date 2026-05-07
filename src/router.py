import logging


def get_camera_context(filename: str, config: dict) -> dict:
    """
    Extracts the camera identifier from the filename and matches it to config.yml filters.
    Falls back to 'default' filter if no specific camera ID matches — use this for
    content footage (GoPro, phone) where filenames don't follow the security cam convention.
    A filter with null vertices means full frame — no spatial filtering applied.
    """
    try:
        base_name = filename.upper().rsplit('.', 1)[0]
        last_char = base_name[-1]
        filters = config.get('filters', {})

        # Only treat a single-character filter key as a real security-cam ID.
        # 'default' (and any other multi-char key) is a fallback bucket, not a camera.
        if last_char in filters and len(last_char) == 1 and last_char != 'DEFAULT':
            filter_key = last_char
            camera_id = last_char
            logging.info(f"Matched {filename} to filter '{last_char}'")
        elif 'default' in filters:
            filter_key = 'default'
            camera_id = None
            logging.info(f"No filter for '{last_char}' in {filename} — using default")
        else:
            logging.warning(f"No filter for '{last_char}' and no default defined — skipping {filename}")
            return None

        vertices = filters[filter_key].get('spatial_mask', {}).get('vertices')

        return {
            'id': filter_key,
            'camera_id': camera_id,  # security-cam letter, or None for non-camera footage
            'file_key': filename,
            'mask_config': {'vertices': vertices},  # None = full frame
        }

    except Exception as e:
        logging.error(f"Routing error for {filename}: {e}")
        return None
