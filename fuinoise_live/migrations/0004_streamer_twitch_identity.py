from django.db import migrations, models
from django.db.models.functions import Lower


def normalize_twitch_logins(apps, schema_editor):
    Streamer = apps.get_model("fuinoise_live", "Streamer")
    seen = {}
    updates = []
    for streamer in Streamer.objects.order_by("pk").iterator():
        login = streamer.twitch_username.strip().lower()
        if login in seen:
            raise ValueError(
                f"Twitch login {login!r} is used by streamer IDs "
                f"{seen[login]} and {streamer.pk}; resolve this before migrating."
            )
        seen[login] = streamer.pk
        if login != streamer.twitch_username:
            updates.append((streamer.pk, login))
    for streamer_id, login in updates:
        Streamer.objects.filter(pk=streamer_id).update(twitch_username=login)


class Migration(migrations.Migration):
    dependencies = [("fuinoise_live", "0003_raidslot_position")]

    operations = [
        migrations.RunPython(normalize_twitch_logins, migrations.RunPython.noop),
        migrations.RemoveField(model_name="streamer", name="twitch_url"),
        migrations.AddField(
            model_name="streamer",
            name="twitch_display_name",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AlterField(
            model_name="streamer",
            name="twitch_id",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
        migrations.AddConstraint(
            model_name="streamer",
            constraint=models.UniqueConstraint(
                Lower("twitch_username"), name="unique_streamer_twitch_login_ci"
            ),
        ),
    ]
