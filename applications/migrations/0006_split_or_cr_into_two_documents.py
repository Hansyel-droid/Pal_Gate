from django.db import migrations, models

import applications.utils


class Migration(migrations.Migration):
    """
    Splits the single `or_cr` upload into the two separate LTO documents it
    was always standing in for: the Official Receipt and the Certificate of
    Registration.

    The existing column is RENAMED rather than dropped and recreated, so no
    uploaded file is lost — a row that held an OR/CR keeps pointing at the
    same path under documents/or_cr/, which still resolves. Only new uploads
    land in the new directories.

    What this migration cannot know is which of the two documents an old
    file actually is: before the split an applicant uploaded one file, often
    a single scan of both. Those land in `official_receipt`, and
    `vehicle_registration` is left empty for every pre-existing row — so an
    application submitted before this change will show its CR as missing on
    the admin's review screen. That is a true statement about what we hold
    (we have one file and cannot prove it is the CR), not a deletion.
    """

    dependencies = [
        ('applications', '0005_stickerapplication_app_status_created_idx_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='stickerapplication',
            old_name='or_cr',
            new_name='official_receipt',
        ),
        migrations.AlterField(
            model_name='stickerapplication',
            name='official_receipt',
            field=models.FileField(
                upload_to=applications.utils.upload_official_receipt
            ),
        ),
        migrations.AddField(
            model_name='stickerapplication',
            name='vehicle_registration',
            # Empty for every existing row — see the note above.
            field=models.FileField(
                default='',
                upload_to=applications.utils.upload_vehicle_registration,
            ),
            preserve_default=False,
        ),
    ]
