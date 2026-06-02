import logging
import os
from dataclasses import dataclass
from typing import Iterable

import aiohttp
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes


OVH_API_URL = "https://eu.api.ovh.com/v1/vps/order/rule/datacenter"
OS_CHOICES = {
    "linux": "Linux",
    "windows": "Windows",
}
VPS_PLANS = {
    "vps-2025-model1": "VPS-1",
    "vps-2025-model2": "VPS-2",
    "vps-2025-model3": "VPS-3",
    "vps-2025-model4": "VPS-4",
    "vps-2025-model5": "VPS-5",
    "vps-2025-model6": "VPS-6",
}


@dataclass(frozen=True)
class DatacenterAvailability:
    name: str
    code: str
    status: str
    linux_status: str
    windows_status: str
    days_before_delivery: int

    def availability_for(self, os_choice: str) -> str:
        if os_choice == "windows":
            return self.windows_status
        return self.linux_status

    def is_available_for(self, os_choice: str) -> bool:
        return self.availability_for(os_choice) == "available"


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
            windows_status=item.get("windowsStatus", "unknown"),
            days_before_delivery=int(item.get("daysBeforeDelivery") or 0),
        )
        for item in datacenters
        if item.get("code", "").startswith("eu-")
    ]


def format_availability(
    datacenters: Iterable[DatacenterAvailability],
    os_choice: str,
    plan_label: str,
) -> str:
    rows = sorted(datacenters, key=lambda item: item.code)
    os_label = OS_CHOICES.get(os_choice, os_choice.title())
    if not rows:
        return "No European datacenter was returned by the OVHcloud API."

    available = [item for item in rows if item.is_available_for(os_choice)]
    header = (
        f"✅ {plan_label} {os_label} is available in Europe:"
        if available
        else f"❌ No {plan_label} {os_label} is currently available in Europe."
    )

    lines = [header, ""]
    for item in rows:
        availability = item.availability_for(os_choice)
        icon = "✅" if item.is_available_for(os_choice) else "❌"
        delivery = (
            f" - delivery D+{item.days_before_delivery}"
            if item.days_before_delivery
            else ""
        )
        lines.append(
            f"{icon} <b>{item.name}</b> ({item.code}) : "
            f"<code>{availability}</code>{delivery}"
        )
    return "\n".join(lines)


def default_settings(application: Application) -> dict[str, str]:
    return {
        "os": application.bot_data["default_os"],
        "plan_code": application.bot_data["default_plan_code"],
    }


def get_chat_settings(application: Application, chat_id_value: int) -> dict[str, str]:
    chat_settings = application.bot_data.setdefault("chat_settings", {})
    return chat_settings.setdefault(chat_id_value, default_settings(application))


def plan_label(plan_code: str) -> str:
    return VPS_PLANS.get(plan_code, plan_code)


def settings_text(settings: dict[str, str]) -> str:
    os_label = OS_CHOICES.get(settings["os"], settings["os"].title())
    return (
        "Choose what to monitor.\n\n"
        f"Current OS: <b>{os_label}</b>\n"
        f"Current VPS type: <b>{plan_label(settings['plan_code'])}</b>"
    )


def settings_key(chat_id_value: int, settings: dict[str, str]) -> str:
    return f"{chat_id_value}:{settings['os']}:{settings['plan_code']}"


def settings_keyboard(settings: dict[str, str]) -> InlineKeyboardMarkup:
    os_buttons = [
        InlineKeyboardButton(
            f"{'✓ ' if key == settings['os'] else ''}{label}",
            callback_data=f"os:{key}",
        )
        for key, label in OS_CHOICES.items()
    ]
    plan_buttons = [
        InlineKeyboardButton(
            f"{'✓ ' if code == settings['plan_code'] else ''}{label}",
            callback_data=f"plan:{code}",
        )
        for code, label in VPS_PLANS.items()
    ]
    return InlineKeyboardMarkup(
        [
            os_buttons,
            plan_buttons[:3],
            plan_buttons[3:],
            [InlineKeyboardButton("Check now", callback_data="action:check")],
        ]
    )


async def run_check(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id_value: int,
) -> tuple[str, bool]:
    settings = get_chat_settings(context.application, chat_id_value)
    plan_code = settings["plan_code"]
    os_choice = settings["os"]
    subsidiary = context.application.bot_data["subsidiary"]
    async with aiohttp.ClientSession() as session:
        datacenters = await fetch_vps_availability(session, plan_code, subsidiary)

    has_available = any(item.is_available_for(os_choice) for item in datacenters)
    return format_availability(datacenters, os_choice, plan_label(plan_code)), has_available


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_chat_settings(context.application, update.effective_chat.id)
    await update.effective_message.reply_text(
        settings_text(settings),
        reply_markup=settings_keyboard(settings),
        parse_mode=ParseMode.HTML,
    )


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    chat_id_value = query.message.chat_id
    settings = get_chat_settings(context.application, chat_id_value)
    kind, value = query.data.split(":", 1)

    if kind == "os" and value in OS_CHOICES:
        settings["os"] = value
    elif kind == "plan" and value in VPS_PLANS:
        settings["plan_code"] = value
    elif kind == "action" and value == "check":
        await query.edit_message_text("Checking OVHcloud availability...")
        try:
            text, _ = await run_check(context, chat_id_value)
        except Exception as exc:
            logging.exception("OVH check failed")
            await query.edit_message_text(f"OVHcloud check failed: {exc}")
            return
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)
        return

    await query.edit_message_text(
        settings_text(settings),
        reply_markup=settings_keyboard(settings),
        parse_mode=ParseMode.HTML,
    )


async def chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(str(update.effective_chat.id))


async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await update.effective_message.reply_text("Checking OVHcloud availability...")
    try:
        text, _ = await run_check(context, update.effective_chat.id)
    except Exception as exc:
        logging.exception("OVH check failed")
        await message.edit_text(f"OVHcloud check failed: {exc}")
        return

    await message.edit_text(text, parse_mode=ParseMode.HTML)


async def notify_if_available(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id_value = context.job.chat_id
    settings = get_chat_settings(context.application, chat_id_value)
    try:
        text, has_available = await run_check(context, chat_id_value)
    except Exception as exc:
        logging.exception("Scheduled OVH check failed")
        await context.bot.send_message(
            chat_id=chat_id_value,
            text=f"OVHcloud check failed: {exc}",
        )
        return

    previous_availability = context.application.bot_data.setdefault(
        "previous_availability",
        {},
    )
    availability_key = settings_key(chat_id_value, settings)
    previous_state = previous_availability.get(availability_key)
    previous_availability[availability_key] = has_available

    if has_available and previous_state is not True:
        await context.bot.send_message(
            chat_id=chat_id_value,
            text=text,
            parse_mode=ParseMode.HTML,
        )


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id_value = update.effective_chat.id
    interval = context.application.bot_data["interval"]
    settings = get_chat_settings(context.application, chat_id_value)

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
        f"Monitoring {plan_label(settings['plan_code'])} "
        f"{OS_CHOICES[settings['os']]} every {interval} seconds. "
        "You will be notified when stock appears."
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
    chat_id_int = int(chat_id_value)
    get_chat_settings(application, chat_id_int)
    application.job_queue.run_repeating(
        notify_if_available,
        interval=interval,
        first=0,
        chat_id=chat_id_int,
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
    default_plan_code = os.getenv("OVH_PLAN_CODE", "vps-2025-model1")
    if default_plan_code not in VPS_PLANS:
        raise RuntimeError(f"Unsupported OVH_PLAN_CODE: {default_plan_code}")

    default_os = os.getenv("OVH_OS", "linux").lower()
    if default_os not in OS_CHOICES:
        raise RuntimeError("OVH_OS must be either linux or windows")

    application.bot_data["default_plan_code"] = default_plan_code
    application.bot_data["default_os"] = default_os
    application.bot_data["subsidiary"] = os.getenv("OVH_SUBSIDIARY", "FR")
    application.bot_data["interval"] = env_int("CHECK_INTERVAL_SECONDS", 60)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("id", chat_id))
    application.add_handler(CommandHandler("check", check))
    application.add_handler(CommandHandler("watch", watch))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CallbackQueryHandler(handle_selection))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
