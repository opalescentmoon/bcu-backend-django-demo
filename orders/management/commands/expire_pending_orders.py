import time

from django.core.management.base import BaseCommand

from orders.services import expire_overdue_orders


class Command(BaseCommand):
    help = "Menandai pesanan pending yang melewati batas waktu sebagai expired dan mengembalikan stok."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Jalankan terus-menerus (service scheduler).")
        parser.add_argument("--interval", type=int, default=60, help="Jeda antar-putaran dalam detik (default 60).")

    def handle(self, *args, loop=False, interval=60, **options):
        while True:
            count = expire_overdue_orders()
            if count or not loop:
                self.stdout.write(f"{count} pesanan ditandai expired.")
            if not loop:
                return
            time.sleep(interval)
