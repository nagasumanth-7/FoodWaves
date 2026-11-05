from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group
from restaurant.models import MenuCategory, MenuItem


class Command(BaseCommand):
    help = "Seeds initial menu categories, items, and role groups into the database idempotently."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Initializing restaurant database seed..."))

        # 1. Create Role Groups (Section 7)
        roles = ["CUSTOMER", "WAITER", "CHEF", "CASHIER", "MANAGER", "ADMIN"]
        for role_name in roles:
            group, created = Group.objects.get_or_create(name=role_name)
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + Created Role Group: {role_name}"))

        # 2. Categories
        categories_data = [
            ("Starters", "Crisp, fiery, and appetizing South Indian and tandoor starters to awaken your palate."),
            ("Tiffins", "Traditional South Indian breakfast staples, golden dosas, and steamed idlis."),
            ("Curries", "Slow-simmered rich curries prepared with freshly roasted whole spices."),
            ("Rice & Biryani", "Aromatic long-grain basmati biryanis layered with delicate saffron and spices."),
            ("Beverages", "Refreshing handcrafted drinks, cooling buttermilk, and artisanal South Indian coffees."),
            ("Desserts", "Warm artisanal sweets and decadent post-meal delicacies."),
        ]

        cat_instances = {}
        for cat_name, cat_desc in categories_data:
            cat_obj, created = MenuCategory.objects.get_or_create(
                name=cat_name,
                defaults={"description": cat_desc, "is_active": True},
            )
            cat_instances[cat_name] = cat_obj
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + Created Category: {cat_name}"))
            else:
                self.stdout.write(f"  = Existing Category: {cat_name}")

        # 3. Initial Menu Items (Section 5)
        menu_items_data = [
            # STARTERS
            {
                "category": "Starters",
                "name": "Pesarattu",
                "description": "Nutritious, crispy golden crepe crafted from soaked green gram batter and seasoned with fresh green chillies and cumin. Served steaming hot with zesty allam (ginger) pachadi and fresh coconut relish.",
                "price": Decimal("140.00"),
                "preparation_time": 10,
            },
            {
                "category": "Starters",
                "name": "Andhra Chicken 65",
                "description": "Fiery and crisp boneless country chicken morsels marinated in crushed Guntur red chillies, ginger-garlic paste, and heritage spices, flash-tossed with fresh curry leaves and slivered garlic.",
                "price": Decimal("290.00"),
                "preparation_time": 15,
            },
            {
                "category": "Starters",
                "name": "Paneer Tikka",
                "description": "Prime malai cottage cheese cubes infused in mustard oil, roasted cumin, and thick hung curd marinade, skewer-roasted over glowing charcoal until smoky with blistered bell peppers and red onions.",
                "price": Decimal("240.00"),
                "preparation_time": 15,
            },
            {
                "category": "Starters",
                "name": "Gobi Manchurian",
                "description": "Crisp cauliflower florets tossed in a blazing wok with ginger, garlic, spring onions, and a sticky sweet-savory Indo-Chinese garlic soya reduction.",
                "price": Decimal("210.00"),
                "preparation_time": 15,
            },

            # TIFFINS
            {
                "category": "Tiffins",
                "name": "Plain Dosa",
                "description": "Ultra-crisp, wafer-thin golden fermented rice and urad dal crepe cooked on an artisanal cast-iron griddle with pure cow ghee. Accompanied by fragrant lentil sambar and freshly ground coconut chutney.",
                "price": Decimal("80.00"),
                "preparation_time": 8,
            },
            {
                "category": "Tiffins",
                "name": "Masala Dosa",
                "description": "Signature coastal crepe roasted golden-brown in country ghee, folded around an aromatic turmeric-spiced potato and onion filling, served alongside fresh coconut relish and piping hot drumstick sambar.",
                "price": Decimal("120.00"),
                "preparation_time": 10,
            },
            {
                "category": "Tiffins",
                "name": "Idly",
                "description": "Fluffy, feather-light steamed cakes prepared from slowly stone-ground rice and urad dal batter. Served piping hot with spiced vegetable sambar and spicy Andhra tomato-coconut pachadi.",
                "price": Decimal("70.00"),
                "preparation_time": 8,
            },
            {
                "category": "Tiffins",
                "name": "Vada",
                "description": "Traditional South Indian medu vadas, crispy and golden on the outside with an airy, soft spiced center studded with whole black peppercorns, fresh ginger, and curry leaves.",
                "price": Decimal("70.00"),
                "preparation_time": 8,
            },

            # CURRIES
            {
                "category": "Curries",
                "name": "Butter Chicken",
                "description": "Char-grilled tandoori chicken tikka simmered in a velvety, satin gravy of ripe Roma tomatoes, cashew butter, honey, and aromatic roasted kasoori methi with fresh cream.",
                "price": Decimal("280.00"),
                "preparation_time": 20,
            },
            {
                "category": "Curries",
                "name": "Paneer Butter Masala",
                "description": "Handmade soft cottage cheese cubes simmered in a creamy, mild tomato-cashew reduction delicately scented with green cardamom, mace, and crushed fenugreek leaves.",
                "price": Decimal("240.00"),
                "preparation_time": 20,
            },
            {
                "category": "Curries",
                "name": "Andhra Chicken Curry",
                "description": "Heritage Andhra country chicken simmered slowly in a deeply spiced rustic masala paste made of roasted coriander, poppy seeds, cloves, and fiery Guntur chillies.",
                "price": Decimal("290.00"),
                "preparation_time": 25,
            },
            {
                "category": "Curries",
                "name": "Dal Tadka",
                "description": "Arhar and moong yellow lentils slow-cooked till creamy, then finished with a crackling tadka of golden fried garlic, cumin seeds, whole red chillies, and aromatic desi cow ghee.",
                "price": Decimal("160.00"),
                "preparation_time": 15,
            },

            # RICE & BIRYANI
            {
                "category": "Rice & Biryani",
                "name": "Chicken Biryani",
                "description": "Royal Hyderabadi dum biryani featuring fragrant aged long-grain basmati rice layered over saffron-infused, tender marinated chicken, caramelized onions, and fresh garden mint.",
                "price": Decimal("280.00"),
                "preparation_time": 25,
            },
            {
                "category": "Rice & Biryani",
                "name": "Mutton Biryani",
                "description": "Succulent tender cuts of premium mutton marinated overnight in spiced yogurt and layered with aromatic aged basmati rice, slow-cooked in a sealed handi under gentle dum.",
                "price": Decimal("350.00"),
                "preparation_time": 30,
            },
            {
                "category": "Rice & Biryani",
                "name": "Veg Biryani",
                "description": "Heirloom basmati rice infused with whole cinnamon, star anise, and saffron, slow-cooked with fresh garden vegetables, mint leaves, and toasted cashew nuts.",
                "price": Decimal("220.00"),
                "preparation_time": 20,
            },

            # BEVERAGES
            {
                "category": "Beverages",
                "name": "Fresh Lime Soda",
                "description": "Hand-pressed zesty lime juice stirred with chilled sparkling soda, available custom-blended sweet, salted, or mixed to invigorate your senses.",
                "price": Decimal("80.00"),
                "preparation_time": 5,
            },
            {
                "category": "Beverages",
                "name": "Mango Lassi",
                "description": "Luscious, thick smoothie churned from velvety fresh whole-milk curd, sun-ripened Ratnagiri Alphonso mango pulp, and a delicate pinch of freshly ground cardamom.",
                "price": Decimal("100.00"),
                "preparation_time": 5,
            },
            {
                "category": "Beverages",
                "name": "Cold Coffee",
                "description": "Artisanal chilled brew crafted from bold South Indian espresso beans blended with creamy whole milk and crowned with a scoop of Madagascar vanilla bean ice cream.",
                "price": Decimal("120.00"),
                "preparation_time": 7,
            },
            {
                "category": "Beverages",
                "name": "Mineral Water",
                "description": "Pure, refreshingly chilled natural mineral spring water, packaged and sealed for pristine clarity.",
                "price": Decimal("30.00"),
                "preparation_time": 1,
            },

            # DESSERTS
            {
                "category": "Desserts",
                "name": "Gulab Jamun",
                "description": "Tender, golden-fried milk-solid dumplings gently soaked in a warm, fragrant syrup infused with rose water, green cardamom pods, and delicate saffron strands.",
                "price": Decimal("110.00"),
                "preparation_time": 5,
            },
        ]

        items_created = 0
        items_existing = 0

        for item_data in menu_items_data:
            cat_obj = cat_instances[item_data["category"]]
            item_obj, created = MenuItem.objects.get_or_create(
                category=cat_obj,
                name=item_data["name"],
                defaults={
                    "description": item_data["description"],
                    "price": item_data["price"],
                    "preparation_time": item_data["preparation_time"],
                    "is_active": True,
                    "is_available": True,
                },
            )
            if created:
                items_created += 1
                self.stdout.write(self.style.SUCCESS(f"  + Added Menu Item: {item_obj.name} (₹{item_obj.price})"))
            else:
                items_existing += 1
                # Keep rich descriptive text updated even if item already exists
                if item_obj.description != item_data["description"]:
                    item_obj.description = item_data["description"]
                    item_obj.save(update_fields=["description"])
                self.stdout.write(f"  = Verified Menu Item: {item_obj.name}")

        # 4. Initial Restaurant Tables
        tables_data = [
            ("T-01", 2, "WINDOW"),
            ("T-02", 2, "WINDOW"),
            ("T-03", 2, "WINDOW"),
            ("T-04", 4, "INDOOR"),
            ("T-05", 4, "INDOOR"),
            ("T-06", 4, "INDOOR"),
            ("T-07", 6, "INDOOR"),
            ("T-08", 6, "INDOOR"),
            ("T-09", 4, "TERRACE"),
            ("T-10", 4, "TERRACE"),
            ("T-11", 8, "BOOTH"),
            ("T-12", 8, "BOOTH"),
        ]

        from restaurant.models import RestaurantTable
        tables_created = 0
        for t_num, cap, area in tables_data:
            t_obj, created = RestaurantTable.objects.get_or_create(
                table_number=t_num,
                defaults={"capacity": cap, "floor_area": area, "status": "AVAILABLE", "is_active": True},
            )
            if created:
                tables_created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSeed completed successfully: {items_created} items created, {items_existing} items verified, {tables_created} tables created."
            )
        )


