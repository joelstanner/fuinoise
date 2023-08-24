from django.contrib import admin
from django.db import models


class Streamer(models.Model):
    display_name = models.CharField(max_length=80)
    twitch_username = models.CharField(max_length=80)
    twitch_url = models.URLField()
    homepage = models.URLField(blank=True)
    twitch_id = models.IntegerField(blank=True)

    def __str__(self):
        return self.display_name

class Event(models.Model):
    date = models.DateField("Calendar Start Date of the event")
    name = models.CharField(default="Raid Train", max_length=255)
    streamers = models.ManyToManyField(Streamer, through="RaidSlot")
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class RaidSlot(models.Model):
    streamer = models.ForeignKey(Streamer, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    start = models.DateTimeField()
    end = models.DateTimeField()
    replay_url = models.URLField(blank=True)

    def __str__(self):
        return print(f"{self.streamer}-{self.event}")
