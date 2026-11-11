"""Demo data for trying the app out (``flask --app run seed-demo``)."""

import random
from datetime import date, timedelta

from .models import (
    ACTION_CHECK_IN,
    ACTION_CHECK_OUT,
    STATUS_AVAILABLE,
    STATUS_CHECKED_OUT,
    STATUS_MAINTENANCE,
    Asset,
    Category,
    Location,
    ROLE_ADMIN,
    Transaction,
    User,
    db,
    utcnow,
)


def seed():
    rng = random.Random(42)
    cats = {}
    for name, days in [("Laptop", 14), ("Tablet", 7), ("Camera", 3),
                       ("Projector", 1), ("Tools", 7)]:
        cats[name] = Category.query.filter_by(name=name).first() or Category(
            name=name, default_loan_days=days)
        db.session.add(cats[name])
    locs = {}
    for name in ["IT Storeroom", "Front Desk", "Lab 2"]:
        locs[name] = Location.query.filter_by(name=name).first() or Location(name=name)
        db.session.add(locs[name])

    if not User.query.filter_by(role=ROLE_ADMIN).first():
        admin = User(username="admin", full_name="Demo Admin", role=ROLE_ADMIN,
                     badge_uid="BADGE0000")
        admin.set_password("adminadmin")
        db.session.add(admin)

    users = []
    for i, (username, full, dept) in enumerate([
        ("alice", "Alice Nguyen", "Engineering"),
        ("bob", "Bob Martinez", "Marketing"),
        ("carol", "Carol Smith", "Operations"),
        ("dave", "Dave Okafor", "Engineering"),
    ]):
        u = User.query.filter_by(username=username).first()
        if u is None:
            u = User(username=username, full_name=full, department=dept,
                     email=f"{username}@example.com", badge_uid=f"BADGE{i + 1:04d}")
            u.set_password("password")
            db.session.add(u)
        users.append(u)

    specs = [
        ("Laptop", "Dell Latitude 7440", "Dell"),
        ("Laptop", "MacBook Pro 14", "Apple"),
        ("Laptop", "ThinkPad X1 Carbon", "Lenovo"),
        ("Tablet", "iPad Air", "Apple"),
        ("Tablet", "Galaxy Tab S9", "Samsung"),
        ("Camera", "Canon EOS R6", "Canon"),
        ("Projector", "Epson EB-L200", "Epson"),
        ("Tools", "Fluke 117 Multimeter", "Fluke"),
    ]
    assets = []
    n = 0
    for copies in range(2):
        for cat, model, maker in specs:
            n += 1
            tag = f"A-{n:05d}"
            a = Asset.query.filter_by(asset_tag=tag).first()
            if a is None:
                a = Asset(
                    asset_tag=tag, name=f"{model} #{copies + 1}", model=model,
                    manufacturer=maker, category=cats[cat],
                    location=rng.choice(list(locs.values())),
                    nfc_uid=f"04{n:02X}A1B2C3D4E5", serial_number=f"SN{rng.randint(10**7, 10**8)}",
                    purchase_date=date.today() - timedelta(days=rng.randint(60, 900)),
                    purchase_cost=rng.choice([299, 649, 1199, 1899, 2499]),
                    warranty_expires=date.today() + timedelta(days=rng.randint(-60, 400)),
                    status=STATUS_AVAILABLE, condition="good",
                )
                db.session.add(a)
            assets.append(a)
    db.session.flush()

    if Transaction.query.count() == 0:
        now = utcnow()
        for a in assets:
            t = now - timedelta(days=60)
            while True:
                t += timedelta(hours=rng.randint(4, 24 * 6))
                if t >= now:
                    break
                user = rng.choice(users)
                loan_days = a.category.default_loan_days
                due = t + timedelta(days=loan_days)
                db.session.add(Transaction(asset=a, user=user, performed_by=user,
                                           action=ACTION_CHECK_OUT, timestamp=t,
                                           due_at=due, method="scan"))
                back = t + timedelta(hours=rng.randint(1, 24 * (loan_days + 2)))
                if back >= now:
                    a.status, a.holder, a.checked_out_at, a.due_at = (
                        STATUS_CHECKED_OUT, user, t, due)
                    break
                db.session.add(Transaction(asset=a, user=user, performed_by=user,
                                           action=ACTION_CHECK_IN, timestamp=back,
                                           method="scan"))
                t = back
        assets[-1].status = STATUS_MAINTENANCE if assets[-1].status == STATUS_AVAILABLE \
            else assets[-1].status
    db.session.commit()
