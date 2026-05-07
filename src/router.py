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
        if not base_name:
            logging.warning(f"Filename has no stem — skipping {filename}")
            return None

        last_char = base_name[-1]
        filters = config.get('filters', {})

        # Single-character filter key = security-cam letter (F/B/L/R).
        # Anything else (multi-char keys like 'default') is a fallback bucket.
        if last_char in filters and last_char != 'DEFAULT':
            filter_key = last_char
            camera_id = last_char
            vertices = filters[filter_key].get('spatial_mask', {}).get('vertices')
            logging.info(f"Matched {filename} to filter '{last_char}'")
        else:
            # Non-camera footage: use configured 'default' if present, otherwise
            # fall back to full-frame (null vertices) so a missing default block
            # in config.yml doesn't silently drop content videos.
            filter_key = 'default'
            camera_id = None
            vertices = filters.get('default', {}).get('spatial_mask', {}).get('vertices')
            if 'default' in filters:
                logging.info(f"No filter for '{last_char}' in {filename} — using default")
            else:
                logging.info(f"No filter for '{last_char}' in {filename} — using implicit full-frame default")

        return {
            'id': filter_key,
            'camera_id': camera_id,  # security-cam letter, or None for non-camera footage
            'file_key': filename,
            'mask_config': {'vertices': vertices},  # None = full frame
        }

    except Exception as e:
        logging.error(f"Routing error for {filename}: {e}")
        return None
