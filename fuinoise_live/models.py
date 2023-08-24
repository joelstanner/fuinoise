import time

from django.utils import timezone
from django.contrib import admin
from django.db import models


class Streamer(models.Model):
    display_name = models.CharField(max_length=80)
    twitch_username = models.CharField(max_length=80)
    twitch_url = models.URLField()
    homepage = models.URLField(default="", blank=True)
    twitch_id = models.IntegerField(default=0, blank=True)

    def __str__(self):
        return self.display_name


class Event(models.Model):
    date = models.DateField("Calendar Start Date of the event")
    name = models.CharField(default="Raid Train", max_length=255)
    streamers = models.ManyToManyField(Streamer, through="RaidSlot")
    description = models.TextField(
        "Are there any special themes or other information specific to this event?",
        default="",
        blank=True,
    )

    def __str__(self):
        return f"{self.name} - {self.date.strftime('%Y/%b/%d')}"


class RaidSlot(models.Model):
    streamer = models.ForeignKey(Streamer, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    start = models.DateTimeField()
    end = models.DateTimeField()
    replay_url = models.URLField(default="", blank=True)

    def __str__(self):
        return f"""{self.streamer.display_name} - 
            {self.event.name} - {self.event.date.strftime('%Y/%b/%d')} - {self.start}"""
