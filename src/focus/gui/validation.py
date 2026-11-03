"""Expose configuration validation errors to the existing main-GUI HTTP contract."""


def _validate_config_safe(config: object) -> list[str]:
    """Run parse_config and return a list of error strings (empty if valid)."""
    import focus.config.validation as validation

    try:
        validation.parse_config(config)
        return []
    except (TypeError, KeyError, ValueError, FileNotFoundError, PermissionError) as e:
        return [str(e)]
