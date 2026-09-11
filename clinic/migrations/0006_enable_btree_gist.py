from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("clinic", "0004_appointment"),
    ]

    operations = [
        BtreeGistExtension(),
    ]
