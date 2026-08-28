def notifications(request):
    """
    Makes the unread notification count available to every template
    without every view having to remember to pass it — the topbar bell in
    base.html renders on every authenticated page, so this has to be a
    context processor rather than something each view opts into.
    """
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}
    return {
        'unread_notification_count': user.notifications.filter(is_read=False).count(),
    }
