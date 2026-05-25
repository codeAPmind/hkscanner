"""
APScheduler 方案：常驻进程，每个交易日 16:35（香港时间）自动触发扫描。
替代方案见 README：用系统 cron/计划任务直接调用 run_scan.py。
"""
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from run_scan import run_full_scan

scheduler = AsyncIOScheduler(timezone="Asia/Hong_Kong")


@scheduler.scheduled_job('cron', hour=16, minute=35, day_of_week='mon-fri')
async def job():
    await run_full_scan()


if __name__ == "__main__":
    scheduler.start()
    print("调度器已启动，每个交易日 16:35 (HKT) 触发扫描")
    asyncio.get_event_loop().run_forever()
