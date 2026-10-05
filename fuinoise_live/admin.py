import json
import re
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import admin
from django.db import transaction
from django.db.models import F
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
from .scheduling import event_local_start, validate_intervals


class EventLocalTimeField(forms.TimeField):
    def to_python(self, value: Any) -> Any:
        if isinstance(value, str):
            shorthand = re.fullmatch(
                r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])m?\s*", value, re.IGNORECASE
            )
            if shorthand:
                hour, minute, meridian = shorthand.groups()
                value = f"{hour}:{minute or '00'} {meridian.upper()}M"
        return super().to_python(value)


class RaidSlotInlineForm(forms.ModelForm):
    duration_minutes = forms.IntegerField(
        label="Planned minutes",
        min_value=1,
        required=False,
        help_text="New slots use the event default. Review older slots explicitly.",
    )
    start_date = forms.DateField(
        label="Date",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Defaults to the event date; change for an overnight slot.",
    )
    start_time = EventLocalTimeField(
        label="Start time (event time zone)",
        required=False,
        input_formats=[
            "%I:%M %p",
            "%I:%M:%S %p",
            "%I:%M:%S.%f %p",
            "%H:%M",
            "%H:%M:%S",
            "%H:%M:%S.%f",
        ],
        widget=forms.TextInput(attrs={"placeholder": "11a or 11:30 AM", "size": 12}),
        help_text="Enter a time such as 11a or 11:30 AM in the event time zone above.",
    )

    class Meta:
        model = RaidSlot
        exclude = ("start",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.event = kwargs.pop("event", None)
        super().__init__(*args, **kwargs)
        self.fields["streamer"].empty_label = "Open slot"
        if self.instance.pk and self.event:
            local_start = timezone.localtime(
                self.instance.start, ZoneInfo(self.event.event_time_zone)
            )
            self.initial["start_date"] = local_start.date()
            if local_start.microsecond:
                time_format = "%I:%M:%S.%f %p"
            elif local_start.second:
                time_format = "%I:%M:%S %p"
            else:
                time_format = "%I:%M %p"
            self.initial["start_time"] = local_start.strftime(time_format)
        elif self.event and self.event.date:
            self.initial["start_date"] = self.event.date
            self.initial["duration_minutes"] = self.event.default_slot_duration_minutes

    def has_changed(self) -> bool:
        if not self.instance.pk and not set(self.changed_data) - {
            "start_date",
            "duration_minutes",
        }:
            return False
        return bool(super().has_changed())

    def clean(self) -> Any:
        cleaned_data = super().clean()
        if self.cleaned_data.get("DELETE"):
            return cleaned_data
        if cleaned_data.get("duration_minutes") is None:
            cleaned_data["duration_minutes"] = (
                self.instance.duration_minutes
                if self.instance.pk
                else self.event.default_slot_duration_minutes if self.event else 60
            )
        start_date = cleaned_data.get("start_date") or (
            self.event.date if self.event else None
        )
        start_time = cleaned_data.get("start_time")
        if not start_time:
            self.add_error("start_time", "Enter a start time.")
        if not start_date or not start_time or not self.event:
            return cleaned_data
        try:
            local_start = event_local_start(
                start_date, start_time, self.event.event_time_zone
            )
        except forms.ValidationError as error:
            self.add_error("start_time", error)
        else:
            self.instance.start = local_start
            cleaned_data["start"] = local_start
        return cleaned_data

    def validate_constraints(self) -> None:
        # Swaps are checked against the final formset state and saved atomically.
        pass

    def save(self, commit: bool = True) -> Any:
        slot = super().save(commit=False)
        if (
            slot.signup_request_id
            and slot.signup_request.streamer_id != slot.streamer_id
        ):
            slot.signup_request = None
        if commit:
            slot.save()
            self.save_m2m()
        return slot


class RaidSlotInlineFormSet(BaseInlineFormSet):
    def get_form_kwargs(self, index: int | None) -> dict[str, Any]:
        return {**super().get_form_kwargs(index), "event": self.instance}

    def clean(self) -> None:
        starts = set()
        intervals = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            start = form.cleaned_data.get("start")
            if start is None:
                continue
            if start in starts:
                raise forms.ValidationError(
                    "Each lineup slot needs a different start time."
                )
            starts.add(start)
            intervals.append((start, form.cleaned_data.get("duration_minutes")))
        super().clean()
        if not any(self.errors):
            validate_intervals(intervals)

    def save(self, commit: bool = True) -> Any:
        if not commit:
            return super().save(commit=False)
        # Move existing rows out of the way so start times can be swapped.
        with transaction.atomic():
            existing = list(self.get_queryset())
            initial_forms = set(self.initial_forms)
            active_forms = [
                form
                for form in self.forms
                if form.cleaned_data
                and not form.cleaned_data.get("DELETE")
                and (form in initial_forms or form.has_changed())
            ]
            if existing:
                last_start = max(
                    [slot.start for slot in existing]
                    + [form.cleaned_data["start"] for form in active_forms]
                )
                for offset, slot in enumerate(existing, 1):
                    RaidSlot.objects.filter(pk=slot.pk).update(
                        start=last_start + timedelta(days=1, seconds=offset),
                    )
            self.changed_objects = []
            self.deleted_objects = []
            self.new_objects = []
            saved = []
            for form in self.initial_forms:
                if form in self.deleted_forms:
                    self.deleted_objects.append(form.instance)
                    form.instance.delete()
            for form in active_forms:
                if form in initial_forms:
                    if form.has_changed():
                        self.changed_objects.append((form.instance, form.changed_data))
                    saved.append(form.save())
                else:
                    new_slot = self.save_new(form)
                    self.new_objects.append(new_slot)
                    saved.append(new_slot)
            return saved


class RaidSlotInline(admin.TabularInline):
    model = RaidSlot
    verbose_name = "lineup slot"
    verbose_name_plural = "Lineup slots — edit the schedule here"
    form = RaidSlotInlineForm
    formset = RaidSlotInlineFormSet
    ordering = ("start", "id")
    autocomplete_fields = ("streamer",)
    fields = (
        "streamer",
        "start_date",
        "start_time",
        "duration_minutes",
        "raid_slot_note",
        "replay_url",
    )
    extra = 1


class EventAdminForm(forms.ModelForm):
    default_slot_duration_minutes = forms.IntegerField(min_value=1, required=False)

    class Meta:
        model = Event
        fields = "__all__"

    class Media:
        js = ("fuinoise_live/event_admin.js",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["event_time_zone"].required = False
            self.initial["event_time_zone"] = ""
            self.fields["event_time_zone"].help_text = (
                "Defaults to the selected community’s time zone; "
                "choose another for this event."
            )
            community_widget = self.fields["community"].widget
            if hasattr(community_widget, "widget"):
                community_widget = community_widget.widget
            community_widget.attrs["data-community-time-zones"] = json.dumps(
                dict(Community.objects.values_list("pk", "default_time_zone"))
            )

    def clean(self) -> Any:
        cleaned_data = super().clean()
        if cleaned_data.get("default_slot_duration_minutes") is None:
            cleaned_data["default_slot_duration_minutes"] = (
                self.instance.default_slot_duration_minutes
            )
        if not self.instance.pk and not cleaned_data.get("event_time_zone"):
            community = cleaned_data.get("community")
            if community:
                cleaned_data["event_time_zone"] = community.default_time_zone
        return cleaned_data


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    form = EventAdminForm
    inlines = (RaidSlotInline,)
    list_display = (
        "name",
        "publication_status",
        "community",
        "date",
        "event_time_zone",
        "slot_count",
    )
    list_filter = ("publication_status", "community", "date", "event_time_zone")
    search_fields = (
        "name",
        "community__name",
        "description",
        "raidslot__streamer__display_name",
    )

    def save_related(
        self, request: Any, form: Any, formsets: Any, change: bool
    ) -> None:
        super().save_related(request, form, formsets, change)
        Event.objects.filter(pk=form.instance.pk).update(
            schedule_version=F("schedule_version") + 1
        )

    @admin.display(description="Slots")
    def slot_count(self, event: Event) -> int:
        return int(event.raidslot_set.count())


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


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "default_time_zone", "website", "logo_url")
    search_fields = ("name", "slug", "description")
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
