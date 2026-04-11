import logging

def get_camera_context(filename: str, config: dict) -> dict:
    """
    Phase 2: Routing
    Extracts the camera identifier from the filename and matches it to config.yml.
    """
    try:
        # Strip the .TS extension and grab the last character
        # Example: "00001873_215437B.TS" -> "00001873_215437B" -> "B"
        base_name = filename.upper().rsplit('.', 1)[0]
        camera_id = base_name[-1]

        filters = config.get('filters', {})

        if camera_id in filters:
            logging.info(f"Matched {filename} to mask configuration '{camera_id}'")
            return {
                'id': camera_id,
                'file_key': filename,
                'mask_config': filters[camera_id]['spatial_mask']
            }
        else:
            logging.warning(f"No mask configuration found in config.yml for ID '{camera_id}' -> {filename}")
            return None

    except Exception as e:
        logging.error(f"Routing Error for {filename}: {e}")
        return None
