from django.contrib import admin
from django.utils import timezone

from .models import Event, Streamer, RaidSlot


class RaidSlotInLine(admin.TabularInline):
    model = RaidSlot
    ordering = ["start"]
    list_display = (
        "streamer",
        "event_time_in_event_timezone",
        "replay_url",
        "raid_slot_note",
    )

    def event_time_in_event_timezone(self, raidslot):
        """display the time for the raid slot in the event timezone with beautiful formatting"""
        fmt = "%-I %p"
        timezone.activate(raidslot.event.event_time_zone)
        dt = raidslot.start.astimezone(timezone.get_current_timezone())
        return dt.strftime(fmt)

    event_time_in_event_timezone.short_description = "Raid Slot Time"


class EventAdmin(admin.ModelAdmin):
    inlines = [RaidSlotInLine]


admin.site.register(Event, EventAdmin)
admin.site.register(Streamer)
