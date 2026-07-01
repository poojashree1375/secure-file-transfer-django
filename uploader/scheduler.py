from apscheduler.schedulers.background import BackgroundScheduler
from .cleanup import delete_expired


def start():
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        delete_expired,
        trigger='interval',
        minutes=1
    )

    scheduler.start()

    print("Scheduler started...")