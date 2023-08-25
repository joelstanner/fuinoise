import datetime
import time
import zoneinfo

from django.utils import timezone
from django.contrib import admin
from django.db import models


EVENT_TIME_ZONE_DEFAULT = zoneinfo.ZoneInfo("US/Pacific")


class Streamer(models.Model):
    display_name = models.CharField(max_length=80, unique=True)
    twitch_username = models.CharField(max_length=80, unique=True)
    twitch_url = models.URLField(unique=True)
    homepage = models.URLField(default="", blank=True)
    twitch_id = models.IntegerField(default=None, blank=True, null=True, unique=True)

    def __str__(self):
        return self.display_name


class Event(models.Model):
    TIMEZONE_CHOICES = sorted(
        zip(
            timezone.zoneinfo.available_timezones(),
            timezone.zoneinfo.available_timezones(),
        )
    )

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
        choices=TIMEZONE_CHOICES,
        max_length=80,
    )

    @admin.display()
    def __str__(self):
        return f"{self.name} - {self.date.strftime('%Y/%b/%d')}"


class RaidSlot(models.Model):
    streamer = models.ForeignKey(Streamer, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    start = models.DateTimeField()
    replay_url = models.URLField(default="", blank=True)
    raid_slot_note = models.CharField(
        "Notes",
        default="",
        blank=True,
        max_length=255,
    )

    def __str__(self):
        return f"""{self.streamer.display_name} - 
            {self.event.name} - {self.event.date.strftime('%Y/%b/%d')} - {self.start}"""
