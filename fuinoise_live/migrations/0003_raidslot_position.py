from django.db import migrations, models


def assign_positions(apps, schema_editor):
    RaidSlot = apps.get_model("fuinoise_live", "RaidSlot")
    last_event_id = None
    position = 0
    for slot in RaidSlot.objects.order_by("event_id", "start", "id").iterator():
        if slot.event_id != last_event_id:
            last_event_id = slot.event_id
            position = 0
        position += 1
        RaidSlot.objects.filter(pk=slot.pk).update(position=position)


class Migration(migrations.Migration):
    dependencies = [("fuinoise_live", "0002_alter_event_event_time_zone")]

    operations = [
        migrations.AddField(
            model_name="raidslot",
            name="position",
            field=models.PositiveIntegerField(
                help_text="Order within this event", null=True
            ),
        ),
        migrations.RunPython(assign_positions, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="raidslot",
            name="position",
            field=models.PositiveIntegerField(help_text="Order within this event"),
        ),
        migrations.AlterModelOptions(
            name="raidslot", options={"ordering": ["event_id", "position", "id"]}
        ),
        migrations.AddConstraint(
            model_name="raidslot",
            constraint=models.UniqueConstraint(
                fields=("event", "position"), name="unique_raid_slot_event_position"
            ),
        ),
    ]
