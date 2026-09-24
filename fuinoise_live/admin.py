from django.contrib import admin
from django.utils import timezone

from .models import Event, RaidSlot, Streamer


class RaidSlotInLine(admin.TabularInline):
    model = RaidSlot
    ordering = ["start"]
    list_display = (
        "streamer",
        "event_time_in_event_timezone",
        "replay_url",
        "raid_slot_note",
    )

    @admin.display(description="Raid Slot Time")
    def event_time_in_event_timezone(self, raidslot: RaidSlot) -> str:
        """Display the raid slot time in the event time zone."""
        fmt = "%-I %p"
        timezone.activate(raidslot.event.event_time_zone)
        dt = raidslot.start.astimezone(timezone.get_current_timezone())
        return str(dt.strftime(fmt))


class EventAdmin(admin.ModelAdmin):
    inlines = [RaidSlotInLine]


admin.site.register(Event, EventAdmin)
admin.site.register(Streamer)
