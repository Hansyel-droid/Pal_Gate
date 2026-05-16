from sticker_portal.models import RegistrationPeriod

def registration_status(request):
    return {'registration_open': RegistrationPeriod.is_open()}