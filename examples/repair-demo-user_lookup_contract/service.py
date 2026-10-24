from repository import get_user

def display_name(users, user_id):
    user = get_user(user_id, users)
    return user['name'] if user is not None else None
