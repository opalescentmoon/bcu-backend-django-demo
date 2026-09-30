from decimal import Decimal

from django.db import models


class PlatformSetting(models.Model):
    """Pengaturan platform (singleton, id=1) yang dapat diubah admin."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    default_commission_rate = models.DecimalField(
        "komisi default", max_digits=5, decimal_places=4, default=Decimal("0.1000")
    )
    order_payment_timeout_minutes = models.PositiveSmallIntegerField("batas waktu pembayaran (menit)", default=60)
    download_url_ttl_minutes = models.PositiveSmallIntegerField("masa berlaku link unduhan (menit)", default=15)

    class Meta:
        verbose_name = "pengaturan platform"
        verbose_name_plural = "pengaturan platform"
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="platformsetting_singleton")]

    def __str__(self):
        return "Pengaturan Platform"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
