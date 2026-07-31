def mask_plate(plate):
    """ABC 1234 → ABC **34 (reveals only the last 2 characters)"""
    if not plate or len(plate) < 3:
        return '***'
    visible = plate[-2:]
    return '*' * (len(plate) - 2) + visible if len(plate) > 2 else '***'


def mask_rfid(uid):
    """AB12CD34 → AB12****"""
    if not uid or len(uid) < 4:
        return '****'
    return uid[:4] + '*' * (len(uid) - 4)


def mask_sticker_id(sticker_id):
    """PalSU-A1B2C3D4 → PalSU-****C3D4"""
    if not sticker_id:
        return '—'
    parts = sticker_id.split('-')
    if len(parts) == 2:
        code = parts[1]
        masked = '*' * (len(code) - 4) + code[-4:] if len(code) > 4 else '****'
        return f'PalSU-{masked}'
    return sticker_id


def mask_name(name):
    """Juan dela Cruz → J*** d*** (only initials are shown)"""
    if not name:
        return '—'
    parts = name.split()
    if len(parts) == 1:
        return parts[0][0] + '***'
    return parts[0][0] + '*** ' + parts[1][0] + '***'