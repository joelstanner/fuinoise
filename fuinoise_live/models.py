import datetime

from django.contrib import admin
from django.db import models
from django.utils import timezone


class Event(models.Model):
    start = models.DateTimeField()
    end = models.DateTimeField()
    name = models.CharField(max_length=255)


class Streamer(models.Model):
    name = models.CharField(max_length=255)
    twitch_url = models.URLField()
    homepage = models.URLField()
    twitch_id = models.IntegerField()
