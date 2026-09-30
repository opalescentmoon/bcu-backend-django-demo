from django.db import migrations


def create_setting(apps, schema_editor):
    apps.get_model("core", "PlatformSetting").objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(create_setting, migrations.RunPython.noop)]
