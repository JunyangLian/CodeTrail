def normalize_user_id(value):
    if value is None:
        raise ValueError('user id cannot be None')
    return str(value).strip().casefold()
