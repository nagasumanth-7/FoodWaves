from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group


class Command(BaseCommand):
    help = "Seeds initial role groups into the database idempotently."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Initializing restaurant role groups..."))

        roles = ["CUSTOMER", "WAITER", "CHEF", "CASHIER", "MANAGER", "ADMIN"]
        created_count = 0
        existing_count = 0

        for role_name in roles:
            group, created = Group.objects.get_or_create(name=role_name)
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"  + Created Role Group: {role_name}"))
            else:
                existing_count += 1
                self.stdout.write(f"  = Existing Role Group: {role_name}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nRole seeding completed: {created_count} groups created, {existing_count} groups verified."
            )
        )

