from django.contrib import admin

from .models import Event, Streamer, RaidSlot

admin.site.register(Event)
admin.site.register(Streamer)
admin.site.register(RaidSlot)
