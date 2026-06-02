import logging
import os
from dataclasses import dataclass
from typing import Iterable

import aiohttp
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes


OVH_API_URL = "https://eu.api.ovh.com/v1/vps/order/rule/datacenter"


@dataclass(frozen=True)
class DatacenterAvailability:
    name: str
    code: str
    status: str
    linux_status: str
    days_before_delivery: int

    @property
    def is_linux_available(self) -> bool:
        return self.linux_status == "available"


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


async def fetch_vps_availability(
    session: aiohttp.ClientSession,
    plan_code: str,
    subsidiary: str,
) -> list[DatacenterAvailability]:
    params = {"ovhSubsidiary": subsidiary, "planCode": plan_code}
    async with session.get(OVH_API_URL, params=params, timeout=20) as response:
        response.raise_for_status()
        payload = await response.json()

    datacenters = payload.get("datacenters", [])
    return [
        DatacenterAvailability(
            name=item.get("datacenter", "unknown"),
            code=item.get("code", ""),
            status=item.get("status", "unknown"),
            linux_status=item.get("linuxStatus", "unknown"),
            days_before_delivery=int(item.get("daysBeforeDelivery") or 0),
        )
        for item in datacenters
        if item.get("code", "").startswith("eu-")
    ]


def format_availability(datacenters: Iterable[DatacenterAvailability]) -> str:
    rows = sorted(datacenters, key=lambda item: item.code)
    if not rows:
        return "No European datacenter was returned by the OVHcloud API."

    available = [item for item in rows if item.is_linux_available]
    header = (
        "✅ VPS-1 Linux is available in Europe:"
        if available
        else "❌ No VPS-1 Linux is currently available in Europe."
    )

    lines = [header, ""]
    for item in rows:
        icon = "✅" if item.is_linux_available else "❌"
        delivery = (
            f" - delivery D+{item.days_before_delivery}"
            if item.days_before_delivery
            else ""
        )
        lines.append(
            f"{icon} <b>{item.name}</b> ({item.code}) : "
            f"<code>{item.linux_status}</code>{delivery}"
        )
    return "\n".join(lines)


async def run_check(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, bool]:
    plan_code = context.application.bot_data["plan_code"]
    subsidiary = context.application.bot_data["subsidiary"]
    async with aiohttp.ClientSession() as session:
        datacenters = await fetch_vps_availability(session, plan_code, subsidiary)

    has_available = any(item.is_linux_available for item in datacenters)
    return format_availability(datacenters), has_available


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Commands: /check to check now, /watch to monitor, /stop to stop monitoring, /id to show the current chat ID."
    )


async def chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(str(update.effective_chat.id))


async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await update.effective_message.reply_text("Checking OVHcloud availability...")
    try:
        text, _ = await run_check(context)
    except Exception as exc:
        logging.exception("OVH check failed")
        await message.edit_text(f"OVHcloud check failed: {exc}")
        return

    await message.edit_text(text, parse_mode=ParseMode.HTML)


async def notify_if_available(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id_value = context.job.chat_id
    try:
        text, has_available = await run_check(context)
    except Exception as exc:
        logging.exception("Scheduled OVH check failed")
        await context.bot.send_message(
            chat_id=chat_id_value,
            text=f"OVHcloud check failed: {exc}",
        )
        return

    previous_state = context.application.bot_data.get("previous_available")
    context.application.bot_data["previous_available"] = has_available

    if has_available and previous_state is not True:
        await context.bot.send_message(
            chat_id=chat_id_value,
            text=text,
            parse_mode=ParseMode.HTML,
        )


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id_value = update.effective_chat.id
    interval = context.application.bot_data["interval"]

    for job in context.job_queue.get_jobs_by_name(str(chat_id_value)):
        job.schedule_removal()

    context.job_queue.run_repeating(
        notify_if_available,
        interval=interval,
        first=0,
        chat_id=chat_id_value,
        name=str(chat_id_value),
    )
    await update.effective_message.reply_text(
        f"Monitoring enabled every {interval} seconds. You will be notified when stock appears."
    )


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    jobs = context.job_queue.get_jobs_by_name(str(update.effective_chat.id))
    for job in jobs:
        job.schedule_removal()

    await update.effective_message.reply_text("Monitoring stopped.")


async def post_init(application: Application) -> None:
    chat_id_value = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id_value:
        return

    interval = application.bot_data["interval"]
    application.job_queue.run_repeating(
        notify_if_available,
        interval=interval,
        first=0,
        chat_id=int(chat_id_value),
        name=str(chat_id_value),
    )


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    application = Application.builder().token(token).post_init(post_init).build()
    application.bot_data["plan_code"] = os.getenv("OVH_PLAN_CODE", "vps-2025-model1")
    application.bot_data["subsidiary"] = os.getenv("OVH_SUBSIDIARY", "FR")
    application.bot_data["interval"] = env_int("CHECK_INTERVAL_SECONDS", 60)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("id", chat_id))
    application.add_handler(CommandHandler("check", check))
    application.add_handler(CommandHandler("watch", watch))
    application.add_handler(CommandHandler("stop", stop))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
