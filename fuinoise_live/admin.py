from django.contrib import admin

from .models import Event, Streamer, RaidSlot


class RaidSlotInLine(admin.TabularInline):
    model = RaidSlot
    ordering = ["start"]


class EventAdmin(admin.ModelAdmin):
    inlines = [RaidSlotInLine]


admin.site.register(Event, EventAdmin)
admin.site.register(Streamer)
