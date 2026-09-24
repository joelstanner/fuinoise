import zoneinfo

from django.contrib import admin
from django.db import models
from django.db.models.functions import Lower

EVENT_TIME_ZONE_DEFAULT = zoneinfo.ZoneInfo("US/Pacific")


def timezone_choices() -> list[tuple[str, str]]:
    return [(name, name) for name in sorted(zoneinfo.available_timezones())]


class Streamer(models.Model):
    # Organizer-chosen name, independent of the Twitch account's display name.
    display_name = models.CharField(max_length=80, unique=True)
    twitch_username = models.CharField(max_length=80, unique=True)
    twitch_display_name = models.CharField(max_length=80, default="", blank=True)
    homepage = models.URLField(default="", blank=True)
    twitch_id = models.CharField(max_length=32, blank=True, null=True, unique=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("twitch_username"), name="unique_streamer_twitch_login_ci"
            )
        ]

    @property
    def twitch_url(self) -> str:
        return f"https://www.twitch.tv/{self.twitch_username}"

    def save(self, *args, **kwargs):
        self.twitch_username = self.twitch_username.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return str(self.display_name)


class Event(models.Model):
    date = models.DateField("Calendar Start Date of the event")
    name = models.CharField(default="Raid Train", max_length=255)
    streamers = models.ManyToManyField(Streamer, through="RaidSlot")
    description = models.TextField(
        "Are there any special themes or other information specific to this event?",
        default="",
        blank=True,
    )
    event_time_zone = models.CharField(
        "Time zone that is considered the home timezone for the event",
        default=str(EVENT_TIME_ZONE_DEFAULT),
        choices=timezone_choices,
        max_length=80,
    )

    @admin.display()
    def __str__(self) -> str:
        return f"{self.name} - {self.date.strftime('%Y/%b/%d')}"


class RaidSlot(models.Model):
    streamer = models.ForeignKey(Streamer, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    position = models.PositiveIntegerField(help_text="Order within this event")
    start = models.DateTimeField()
    replay_url = models.URLField(default="", blank=True)
    raid_slot_note = models.CharField(
        "Notes",
        default="",
        blank=True,
        max_length=255,
    )

    class Meta:
        ordering = ["event_id", "position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "position"], name="unique_raid_slot_event_position"
            )
        ]

    def __str__(self) -> str:
        return f"""{self.streamer.display_name} - 
            {self.event.name} - {self.event.date.strftime('%Y/%b/%d')} - {self.start}"""
