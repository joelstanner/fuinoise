import django.db.models.deletion
from django.db import migrations, models


def assign_default_community(apps, schema_editor):
    Community = apps.get_model("fuinoise_live", "Community")
    Event = apps.get_model("fuinoise_live", "Event")
    community, _ = Community.objects.using(
        schema_editor.connection.alias
    ).get_or_create(slug="fuinoise", defaults={"name": "Fuinoise"})
    Event.objects.using(schema_editor.connection.alias).filter(
        community__isnull=True
    ).update(community=community)


class Migration(migrations.Migration):
    dependencies = [
        (
            "fuinoise_live",
            "0005_community_genre_instrument_event_organizer_notes_and_more",
        )
    ]

    operations = [
        migrations.RunPython(assign_default_community, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="event",
            name="community",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="events",
                to="fuinoise_live.community",
            ),
        ),
    ]
