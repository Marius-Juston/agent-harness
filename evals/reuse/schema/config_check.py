def problems(data: object) -> list[str]:
    """Every way data violates config.schema.json (next to this file, JSON Schema draft 2020-12), one message each;
    [] when data is valid. The schema will grow, so the check must follow whatever it says."""
    raise NotImplementedError
