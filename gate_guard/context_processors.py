from .models import SystemConfig

def system_config(request):
    return {'system_config': SystemConfig.load()}