from zoneinfo import ZoneInfo

from django import forms
from django.contrib import admin
from django.db import transaction
from django.forms.models import BaseInlineFormSet
from django.utils import timezone

from .models import (
    Community,
    CommunityMembership,
    Event,
    Genre,
    Instrument,
    RaidSlot,
    Streamer,
    WeeklyAvailability,
)


class RaidSlotInlineForm(forms.ModelForm):
    class Meta:
        model = RaidSlot
        fields = "__all__"

    def validate_constraints(self):
        # Swaps are checked against the final formset state and saved atomically.
        pass


class RaidSlotInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        positions = set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            position = form.cleaned_data.get("position")
            if position is None:
                continue
            if position in positions:
                raise forms.ValidationError("Each slot needs a distinct position.")
            positions.add(position)

    def save(self, commit=True):
        if not commit:
            return super().save(commit=False)
        # Temporarily move existing rows so positions can be swapped.
        with transaction.atomic():
            existing = list(self.get_queryset())
            temporary_start = (
                max(
                    [slot.position for slot in existing]
                    + [form.cleaned_data.get("position", 0) or 0 for form in self.forms]
                    + [0]
                )
                + 1
            )
            for offset, slot in enumerate(existing):
                RaidSlot.objects.filter(pk=slot.pk).update(
                    position=temporary_start + offset
                )
            self.changed_objects = []
            self.deleted_objects = []
            saved = []
            for form in self.initial_forms:
                if form in self.deleted_forms:
                    self.deleted_objects.append(form.instance)
                    form.instance.delete()
                else:
                    if form.has_changed():
                        self.changed_objects.append((form.instance, form.changed_data))
                    saved.append(form.save())
            saved.extend(self.save_new_objects())
            return saved


class RaidSlotInline(admin.TabularInline):
    model = RaidSlot
    form = RaidSlotInlineForm
    formset = RaidSlotInlineFormSet
    ordering = ("position", "id")
    fields = (
        "position",
        "streamer",
        "start",
        "handoff_at",
        "event_time_in_event_timezone",
        "raid_slot_note",
        "replay_url",
    )
    readonly_fields = ("event_time_in_event_timezone",)
    extra = 0

    @admin.display(description="Event local time")
    def event_time_in_event_timezone(self, slot: RaidSlot) -> str:
        if not slot.start or not slot.event_id:
            return "—"
        local_start = timezone.localtime(
            slot.start, ZoneInfo(slot.event.event_time_zone)
        )
        return local_start.strftime("%-I:%M %p %Z")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    inlines = (RaidSlotInline,)
    list_display = ("name", "community", "date", "event_time_zone", "slot_count")
    list_filter = ("community", "date", "event_time_zone")
    search_fields = (
        "name",
        "community__name",
        "description",
        "raidslot__streamer__display_name",
    )

    @admin.display(description="Slots")
    def slot_count(self, event: Event) -> int:
        return event.raidslot_set.count()


class WeeklyAvailabilityInline(admin.TabularInline):
    model = WeeklyAvailability
    extra = 0


class CommunityMembershipInline(admin.TabularInline):
    model = CommunityMembership
    fk_name = "streamer"
    extra = 0


@admin.register(Streamer)
class StreamerAdmin(admin.ModelAdmin):
    inlines = (WeeklyAvailabilityInline, CommunityMembershipInline)
    filter_horizontal = ("instruments", "genres")
    list_display = (
        "display_name",
        "twitch_display_name",
        "twitch_username",
        "twitch_id",
        "twitch_url",
        "homepage",
        "time_zone",
        "raid_availability",
    )
    list_filter = (
        "raid_availability",
        "time_zone",
        "instruments",
        "genres",
        "communities",
    )
    search_fields = (
        "display_name",
        "twitch_display_name",
        "twitch_username",
        "twitch_id",
        "instruments__name",
        "genres__name",
        "communities__name",
    )


@admin.register(RaidSlot)
class RaidSlotAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "position",
        "streamer",
        "start",
        "handoff_at",
        "raid_slot_note",
        "replay_url",
    )
    list_filter = ("event", "start")
    search_fields = (
        "event__name",
        "streamer__display_name",
        "streamer__twitch_username",
    )
    ordering = ("event", "position")


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "website")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Instrument, Genre)
class TaxonomyAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(WeeklyAvailability)
class WeeklyAvailabilityAdmin(admin.ModelAdmin):
    list_display = ("streamer", "day_of_week", "start_time", "end_time")
    list_filter = ("day_of_week",)
    search_fields = ("streamer__display_name",)


@admin.register(CommunityMembership)
class CommunityMembershipAdmin(admin.ModelAdmin):
    list_display = ("streamer", "community", "role", "joined_on")
    list_filter = ("community",)
    search_fields = ("streamer__display_name", "community__name", "role")
