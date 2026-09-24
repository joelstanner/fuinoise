import zoneinfo

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

EVENT_TIME_ZONE_DEFAULT = zoneinfo.ZoneInfo("GMT")


def timezone_choices() -> list[tuple[str, str]]:
    return [(name, name) for name in sorted(zoneinfo.available_timezones())]


class Instrument(models.Model):
    name = models.CharField(max_length=80, unique=True)

    def __str__(self) -> str:
        return self.name


class Genre(models.Model):
    name = models.CharField(max_length=80, unique=True)

    def __str__(self) -> str:
        return self.name


class Community(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True, default="")
    website = models.URLField(blank=True, default="")
    logo_url = models.URLField(blank=True, default="")

    class Meta:
        verbose_name_plural = "communities"

    def __str__(self) -> str:
        return self.name


class Streamer(models.Model):
    # Organizer-chosen name, independent of the Twitch account's display name.
    display_name = models.CharField(max_length=80, unique=True)
    twitch_username = models.CharField(max_length=80, unique=True)
    twitch_display_name = models.CharField(max_length=80, default="", blank=True)
    homepage = models.URLField(default="", blank=True)
    twitch_id = models.CharField(max_length=32, blank=True, null=True, unique=True)
    instruments = models.ManyToManyField(
        Instrument, blank=True, related_name="streamers"
    )
    genres = models.ManyToManyField(Genre, blank=True, related_name="streamers")
    time_zone = models.CharField(max_length=80, choices=timezone_choices, default="UTC")
    raid_availability = models.CharField(
        max_length=16,
        choices=[
            ("unknown", "Unknown"),
            ("open", "Open to raids"),
            ("limited", "Ask first"),
            ("unavailable", "Unavailable"),
        ],
        default="unknown",
    )
    raid_preferences = models.TextField(blank=True, default="")
    organizer_notes = models.TextField(blank=True, default="")
    communities = models.ManyToManyField(
        Community, through="CommunityMembership", blank=True, related_name="streamers"
    )

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
    class PublicationStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        PRIVATE = "private", "Private"

    date = models.DateField("Calendar Start Date of the event")
    name = models.CharField(default="Raid Train", max_length=255)
    publication_status = models.CharField(
        max_length=16,
        choices=PublicationStatus.choices,
        default=PublicationStatus.DRAFT,
        help_text="Only published events appear on the public site.",
    )
    streamers = models.ManyToManyField(Streamer, through="RaidSlot")
    community = models.ForeignKey(
        Community, on_delete=models.PROTECT, related_name="events"
    )
    organizer_notes = models.TextField(blank=True, default="")
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
    streamer = models.ForeignKey(
        Streamer,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text="Leave blank for an open time slot; change this to move a streamer.",
    )
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    position = models.PositiveIntegerField(help_text="Order within this event")
    start = models.DateTimeField()
    handoff_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Planned raid handoff time; leave blank until known",
    )
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

    def clean(self):
        super().clean()
        if self.handoff_at and self.start and self.handoff_at <= self.start:
            raise ValidationError(
                {"handoff_at": "Handoff must be after the slot start."}
            )

    def __str__(self) -> str:
        streamer_name = self.streamer.display_name if self.streamer else "Open slot"
        return f"{streamer_name} - {self.event.name} - {self.start}"


class WeeklyAvailability(models.Model):
    DAYS = [
        (i, day)
        for i, day in enumerate(
            (
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            )
        )
    ]
    streamer = models.ForeignKey(
        Streamer, on_delete=models.CASCADE, related_name="weekly_availability"
    )
    day_of_week = models.PositiveSmallIntegerField(choices=DAYS)
    start_time = models.TimeField()
    end_time = models.TimeField(
        help_text="In the streamer's time zone; may be on the next day"
    )
    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ("streamer_id", "day_of_week", "start_time", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("streamer", "day_of_week", "start_time"),
                name="unique_streamer_weekly_start",
            )
        ]

    def __str__(self) -> str:
        return (
            f"{self.streamer} — {self.get_day_of_week_display()} "
            f"{self.start_time}–{self.end_time}"
        )


class CommunityMembership(models.Model):
    community = models.ForeignKey(
        Community, on_delete=models.CASCADE, related_name="memberships"
    )
    streamer = models.ForeignKey(
        Streamer, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=80, blank=True, default="")
    joined_on = models.DateField(blank=True, null=True)
    organizer_notes = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("community", "streamer"),
                name="unique_community_streamer_membership",
            )
        ]

    def __str__(self) -> str:
        return f"{self.streamer} in {self.community}"
