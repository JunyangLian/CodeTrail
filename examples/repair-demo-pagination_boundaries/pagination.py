def paginate(items, page=1, page_size=3):
    if type(page) is not int or page < 1:
        raise ValueError('page must be a positive integer')
    if type(page_size) is not int or page_size < 1:
        raise ValueError('page_size must be a positive integer')
    total = len(items)
    start = page * page_size
    return {
        'items': items[start:start + page_size],
        'total_pages': total // page_size,
        'has_next': page * page_size <= total,
    }
