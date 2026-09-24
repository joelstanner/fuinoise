from fuinoise_live.models import Community, Event


def create_event(**kwargs):
    community, _ = Community.objects.get_or_create(
        slug="fuinoise", defaults={"name": "Fuinoise"}
    )
    return Event.objects.create(community=community, **kwargs)
