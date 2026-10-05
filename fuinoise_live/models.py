import zoneinfo
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from typing import Any

from django.conf import settings
from django.contrib import admin
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower

EVENT_TIME_ZONE_DEFAULT = zoneinfo.ZoneInfo("GMT")


def timezone_choices() -> list[tuple[str, str]]:
    return [(name, name) for name in sorted(zoneinfo.available_timezones())]


class Instrument(models.Model):
    name = models.CharField(max_length=80, unique=True)

    def __str__(self) -> str:
        return str(self.name)


class Genre(models.Model):
    name = models.CharField(max_length=80, unique=True)

    def __str__(self) -> str:
        return str(self.name)


class Community(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(unique=True)
    default_time_zone = models.CharField(
        "Default raid event time zone",
        max_length=80,
        choices=timezone_choices,
        default="GMT",
    )
    description = models.TextField(blank=True, default="")
    website = models.URLField(blank=True, default="")
    logo_url = models.URLField(blank=True, default="")

    class Meta:
        verbose_name_plural = "communities"

    def __str__(self) -> str:
        return str(self.name)


class Streamer(models.Model):
    # Organizer-chosen name, independent of the Twitch account's display name.
    display_name = models.CharField(
        "Name shown on Fuinoise",
        max_length=80,
        unique=True,
        help_text="This is the name visitors see on event pages.",
    )
    twitch_username = models.CharField(max_length=80, unique=True)
    twitch_display_name = models.CharField(
        "Name shown on Twitch",
        max_length=80,
        default="",
        blank=True,
        help_text="The display name shown on the streamer's Twitch profile.",
    )
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

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.twitch_username = self.twitch_username.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return str(self.display_name)


class StreamerAccount(models.Model):
    class ParticipationStatus(models.TextChoices):
        PENDING = "pending", "Awaiting organizer review"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Not approved"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="streamer_account",
    )
    streamer = models.OneToOneField(
        Streamer, on_delete=models.PROTECT, related_name="account"
    )
    discord_id = models.CharField(max_length=32, unique=True, null=True, blank=True)
    discord_username = models.CharField(max_length=80, blank=True, default="")
    discord_member = models.BooleanField(null=True, blank=True)
    discord_guild_id = models.CharField(max_length=32, blank=True, default="")
    discord_checked_at = models.DateTimeField(null=True, blank=True)
    participation_status = models.CharField(
        max_length=16,
        choices=ParticipationStatus.choices,
        default=ParticipationStatus.PENDING,
    )
    discord_override = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        permissions = [
            ("review_eligibility", "Can review streamer participation"),
            ("override_discord_requirement", "Can override Discord requirement"),
        ]

    def __str__(self) -> str:
        return str(self.streamer)


class EligibilityReview(models.Model):
    account = models.ForeignKey(
        StreamerAccount, on_delete=models.CASCADE, related_name="reviews"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    action = models.CharField(max_length=32)
    participation_status = models.CharField(max_length=16)
    discord_override = models.BooleanField()
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")

    @property
    def action_label(self) -> str:
        return {
            "discord_linked": "Discord connected",
            "discord_unlinked": "Discord disconnected",
            "participation_review": "Participation review",
            "discord_override": "Discord requirement updated",
        }.get(str(self.action), "Account updated")

    @property
    def actor_label(self) -> str:
        if self.actor is None:
            return "Deleted account"
        try:
            return str(self.actor.streamer_account.streamer.display_name)
        except StreamerAccount.DoesNotExist:
            return str(self.actor.get_short_name() or self.actor.username)


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
    default_slot_duration_minutes = models.PositiveIntegerField(
        default=60, validators=[MinValueValidator(1)]
    )
    signup_before_publication = models.BooleanField(default=False)
    schedule_version = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(default_slot_duration_minutes__gt=0),
                name="event_positive_default_duration",
            )
        ]

    @property
    def signup_open(self) -> bool:
        return bool(
            self.publication_status == self.PublicationStatus.PUBLISHED
            or (
                self.publication_status == self.PublicationStatus.DRAFT
                and self.signup_before_publication
            )
        )

    @admin.display()
    def __str__(self) -> str:
        return f"{self.name} - {self.date.strftime('%Y/%b/%d')}"


class RaidSlot(models.Model):
    signup_request = models.ForeignKey(
        "SlotRequest",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="confirmed_slots",
    )
    streamer = models.ForeignKey(
        Streamer,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text="Leave blank for an open time slot; change this to move a streamer.",
    )
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    start = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Planned length. Older slots need duration review before publishing.",
    )
    replay_url = models.URLField(default="", blank=True)
    raid_slot_note = models.CharField(
        "Notes",
        default="",
        blank=True,
        max_length=255,
    )

    class Meta:
        ordering = ["event_id", "start", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "start"], name="unique_raid_slot_event_start"
            ),
            models.CheckConstraint(
                condition=models.Q(duration_minutes__isnull=True)
                | models.Q(duration_minutes__gt=0),
                name="slot_positive_duration",
            ),
        ]

    @property
    def end(self) -> datetime | None:
        if self.duration_minutes is None:
            return None
        start: datetime = self.start
        return start.astimezone(datetime_timezone.utc) + timedelta(
            minutes=self.duration_minutes
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding and self.duration_minutes is None:
            self.duration_minutes = self.event.default_slot_duration_minutes
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        streamer_name = self.streamer.display_name if self.streamer else "Open slot"
        return f"{streamer_name} - {self.event.name} - {self.start}"


class ScheduleDraft(models.Model):
    event = models.OneToOneField(
        Event, on_delete=models.CASCADE, related_name="schedule_draft"
    )
    version = models.PositiveIntegerField(default=0, editable=False)
    base_schedule_version = models.PositiveIntegerField(editable=False)
    base_fingerprint = models.CharField(max_length=64, editable=False)
    date = models.DateField()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    community = models.ForeignKey(Community, on_delete=models.PROTECT)
    event_time_zone = models.CharField(max_length=80, choices=timezone_choices)
    default_slot_duration_minutes = models.PositiveIntegerField(
        default=60, validators=[MinValueValidator(1)]
    )
    signup_before_publication = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(default_slot_duration_minutes__gt=0),
                name="draft_positive_default_duration",
            )
        ]

    def __str__(self) -> str:
        return f"Working draft: {self.name}"


class DraftSlot(models.Model):
    signup_request = models.ForeignKey(
        "SlotRequest",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="draft_slots",
    )
    request_version = models.PositiveIntegerField(null=True, blank=True)
    draft = models.ForeignKey(
        ScheduleDraft, on_delete=models.CASCADE, related_name="slots"
    )
    source_slot = models.ForeignKey(
        RaidSlot, on_delete=models.SET_NULL, blank=True, null=True
    )
    streamer = models.ForeignKey(
        Streamer, on_delete=models.SET_NULL, blank=True, null=True
    )
    start = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)]
    )
    replay_url = models.URLField(blank=True, default="")
    raid_slot_note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ("start", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("draft", "source_slot"), name="draft_unique_source_slot"
            ),
            models.CheckConstraint(
                condition=models.Q(duration_minutes__isnull=True)
                | models.Q(duration_minutes__gt=0),
                name="draft_slot_positive_duration",
            ),
        ]


class SlotRequest(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted for review"
        WITHDRAWN = "withdrawn", "Withdrawn"
        DECLINED = "declined", "Not approved"

    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name="slot_requests"
    )
    streamer = models.ForeignKey(
        Streamer, on_delete=models.PROTECT, related_name="slot_requests"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.SUBMITTED
    )
    notes = models.TextField(blank=True, default="", max_length=2000)
    organizer_notes = models.TextField(blank=True, default="", max_length=2000)
    version = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("event", "streamer"), name="unique_event_streamer_request"
            )
        ]


class SlotPreference(models.Model):
    request = models.ForeignKey(
        SlotRequest, on_delete=models.CASCADE, related_name="preferences"
    )
    slot = models.ForeignKey(
        RaidSlot,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="preferences",
    )
    requested_start = models.DateTimeField()
    requested_duration_minutes = models.PositiveIntegerField(
        validators=[MinValueValidator(1)]
    )

    class Meta:
        ordering = ("requested_start", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("request", "slot"), name="unique_request_preferred_slot"
            ),
            models.CheckConstraint(
                condition=models.Q(requested_duration_minutes__gt=0),
                name="preference_positive_duration",
            ),
        ]


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
