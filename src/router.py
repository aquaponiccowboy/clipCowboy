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
        camera_id = base_name[-1]
        filters = config.get('filters', {})

        if camera_id in filters:
            filter_key = camera_id
            logging.info(f"Matched {filename} to filter '{camera_id}'")
        elif 'default' in filters:
            filter_key = 'default'
            logging.info(f"No filter for '{camera_id}' in {filename} — using default")
        else:
            logging.warning(f"No filter for '{camera_id}' and no default defined — skipping {filename}")
            return None

        vertices = filters[filter_key].get('spatial_mask', {}).get('vertices')

        return {
            'id': filter_key,
            'file_key': filename,
            'mask_config': {'vertices': vertices},  # None = full frame
        }

    except Exception as e:
        logging.error(f"Routing error for {filename}: {e}")
        return None
