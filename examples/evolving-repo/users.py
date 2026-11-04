from directory import normalize_user_id


def lookup_user(records, user_id):
    return records.get(normalize_user_id(user_id))
