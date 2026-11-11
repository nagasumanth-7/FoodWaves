from django.core.management.base import BaseCommand
from django.utils import timezone
from restaurant.models import RestaurantTable, TableSession, Bill


class Command(BaseCommand):
    help = "Make all restaurant tables open (AVAILABLE) and close any active/pending sessions."

    def handle(self, *args, **options):
        now = timezone.now()
        sessions_closed = TableSession.objects.filter(status__in=["ACTIVE", "BILL_PENDING"]).update(
            status="CLOSED",
            closed_at=now,
        )
        bills_settled = Bill.objects.filter(status__in=["OPEN", "REQUESTED", "UNPAID"]).update(
            status="PAID",
            paid_at=now,
        )
        tables_opened = RestaurantTable.objects.all().update(status="AVAILABLE")

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully reset: {tables_opened} tables set to AVAILABLE (open), "
                f"{sessions_closed} sessions closed, {bills_settled} bills settled."
            )
        )

