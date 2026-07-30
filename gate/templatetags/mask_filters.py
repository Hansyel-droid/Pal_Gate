from django import template
from gate.masking import mask_plate, mask_rfid, mask_sticker_id, mask_name

register = template.Library()

register.filter('mask_plate', mask_plate)
register.filter('mask_rfid', mask_rfid)
register.filter('mask_sticker_id', mask_sticker_id)
register.filter('mask_name', mask_name)