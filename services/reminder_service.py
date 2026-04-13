import asyncio
import threading
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import discord

from utils import home_log
try:
    from telegram.error import Forbidden as TelegramForbidden
except ImportError:
    TelegramForbidden = Exception


def _runtime_platform() -> str:
    platform = os.environ.get("DUBOT_RUNTIME", "discord").strip().lower()
    return platform if platform in {"discord", "telegram"} else "discord"


class Reminder:
    def __init__(
        self,
        user_id: int,
        channel_id: int,
        message: str,
        trigger_time: datetime,
        is_dm: bool = False,
        platform: str = "discord",
    ):
        self.user_id = user_id
        self.channel_id = channel_id
        self.message = message
        self.trigger_time = trigger_time
        self.is_dm = is_dm
        self.platform = platform
        self.id = f"{platform}_{user_id}_{int(trigger_time.timestamp())}"
    
    def to_dict(self):
        return {
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "message": self.message,
            "trigger_time": self.trigger_time.isoformat(),
            "is_dm": self.is_dm,
            "platform": self.platform,
            "id": self.id
        }
    
    @classmethod
    def from_dict(cls, data):
        reminder = cls(
            data["user_id"],
            data["channel_id"],
            data["message"],
            datetime.fromisoformat(data["trigger_time"]),
            data.get("is_dm", False),
            data.get("platform", "discord"),
        )
        reminder.id = data.get("id", reminder.id)
        return reminder

class ReminderManager:
    def __init__(self, save_file: str = "reminders.json"):
        self.save_file = save_file
        self.reminders: Dict[str, Reminder] = {}
        self.running = False
        self.thread = None
        self.client = None
        self.telegram_bot = None
        self.platform = _runtime_platform()
        self.loop = None
        self.load()
    
    def set_client(self, client):
        """Set Discord client for sending notifications"""
        self.client = client
        self.telegram_bot = None
        self.platform = "discord"
        self.loop = client.loop if client else None

    def set_telegram_bot(self, bot):
        """Set Telegram bot for sending notifications."""
        self.telegram_bot = bot
        self.client = None
        self.loop = None
        self.platform = "telegram"
    
    def add_reminder(self, user_id: int, channel_id: int, message: str, delay_minutes: int, is_dm: bool = False) -> str:
        """Add a new reminder"""
        trigger_time = datetime.now() + timedelta(minutes=delay_minutes)
        reminder = Reminder(user_id, channel_id, message, trigger_time, is_dm, self.platform)
        self.reminders[reminder.id] = reminder
        self.save()
        return reminder.id
    
    def add_timed_reminder(self, user_id: int, channel_id: int, message: str, trigger_time: datetime, is_dm: bool = False) -> str:
        """Add reminder for specific time"""
        reminder = Reminder(user_id, channel_id, message, trigger_time, is_dm, self.platform)
        self.reminders[reminder.id] = reminder
        self.save()
        return reminder.id
    
    def remove_reminder(self, reminder_id: str) -> bool:
        """Remove a reminder by ID"""
        if reminder_id in self.reminders:
            del self.reminders[reminder_id]
            self.save()
            return True
        return False
    
    def get_user_reminders(self, user_id: int) -> List[Reminder]:
        """Get all reminders for a user"""
        return [r for r in self.reminders.values() if r.user_id == user_id]
    
    def load(self):
        """Load reminders from file"""
        if os.path.exists(self.save_file):
            try:
                with open(self.save_file, 'r') as f:
                    data = json.load(f)
                    for reminder_data in data.get("reminders", []):
                        reminder = Reminder.from_dict(reminder_data)
                        # Only load future reminders
                        if reminder.trigger_time > datetime.now():
                            self.reminders[reminder.id] = reminder
            except (json.JSONDecodeError, KeyError) as e:
                home_log.log_sync(f"Error loading reminders: {e}")
    
    def save(self):
        """Save reminders to file"""
        data = {"reminders": [r.to_dict() for r in self.reminders.values()]}
        with open(self.save_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def check_reminders(self):
        """Check and trigger due reminders"""
        now = datetime.now()
        triggered = []
        
        for reminder_id, reminder in list(self.reminders.items()):
            if reminder.platform != self.platform:
                continue
            if reminder.trigger_time <= now:
                triggered.append(reminder)
                del self.reminders[reminder_id]
        
        if triggered:
            if self.platform == "discord" and self.loop and self.client:
                for reminder in triggered:
                    asyncio.run_coroutine_threadsafe(
                        self._send_reminder(reminder),
                        self.loop
                    )
            elif self.platform == "telegram" and self.telegram_bot:
                for reminder in triggered:
                    asyncio.run(self._send_reminder(reminder))
        
        if triggered:
            self.save()
    
    async def _send_reminder(self, reminder: Reminder):
        """Send a single reminder notification"""
        try:
            if self.platform == "telegram" and self.telegram_bot:
                target_chat_id = reminder.user_id if reminder.is_dm else reminder.channel_id
                await self.telegram_bot.send_message(
                    chat_id=target_chat_id,
                    text=f"⏰ Reminder: {reminder.message}",
                )
                return

            if reminder.is_dm:
                user = await self.client.fetch_user(reminder.user_id)
                await user.send(f"⏰ **Reminder:** {reminder.message}")
            else:
                channel = self.client.get_channel(reminder.channel_id)
                if channel:
                    await channel.send(f"<@{reminder.user_id}> ⏰ **Reminder:** {reminder.message}")
        except TelegramForbidden:
            home_log.log_sync(f"Cannot message Telegram user {reminder.user_id}")
        except Exception as e:
            home_log.log_sync(f"Error sending reminder: {e}")
    
    def start(self):
        """Start the reminder checking thread"""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            home_log.log_sync("✅ Reminder service started")
    
    def stop(self):
        """Stop the reminder checking thread"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        home_log.log_sync("🛑 Reminder service stopped")
    
    def _run(self):
        """Main loop for checking reminders"""
        import time
        while self.running:
            self.check_reminders()
            time.sleep(60)  # Check every minute

# Global instance
reminder_manager = ReminderManager()
