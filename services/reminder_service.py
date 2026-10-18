import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from utils import home_log
from utils.json_store import load_json, save_json
from utils.paths import migrate_root_file

_MAX_SLEEP_SECONDS = 60.0


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
        # Random suffix: two reminders created in the same second must not overwrite each other.
        self.id = f"{platform}_{user_id}_{int(trigger_time.timestamp())}_{uuid.uuid4().hex[:6]}"

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "message": self.message,
            "trigger_time": self.trigger_time.isoformat(),
            "is_dm": self.is_dm,
            "platform": self.platform,
            "id": self.id,
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
    """Reminders persisted to data/reminders.json and delivered by an asyncio task on the bot loop."""

    def __init__(self, save_file: str = migrate_root_file("reminders.json")):
        self.save_file = save_file
        self.reminders: Dict[str, Reminder] = {}
        self.client = None
        self._task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self.load()

    def add_reminder(self, user_id: int, channel_id: int, message: str, delay_minutes: int, is_dm: bool = False) -> str:
        """Add a new reminder"""
        return self.add_timed_reminder(
            user_id, channel_id, message, datetime.now() + timedelta(minutes=delay_minutes), is_dm
        )

    def add_timed_reminder(self, user_id: int, channel_id: int, message: str, trigger_time: datetime, is_dm: bool = False) -> str:
        """Add reminder for specific time"""
        reminder = Reminder(user_id, channel_id, message, trigger_time, is_dm)
        self.reminders[reminder.id] = reminder
        self.save()
        self._wake.set()  # re-evaluate the next due time
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
        """Load future reminders from file"""
        data = load_json(self.save_file, {})
        for reminder_data in data.get("reminders", []) if isinstance(data, dict) else []:
            try:
                reminder = Reminder.from_dict(reminder_data)
            except (KeyError, TypeError, ValueError) as e:
                home_log.log_sync(f"Skipping malformed reminder: {e}")
                continue
            if reminder.trigger_time > datetime.now():
                self.reminders[reminder.id] = reminder

    def save(self):
        """Save reminders to file"""
        save_json(self.save_file, {"reminders": [r.to_dict() for r in self.reminders.values()]})

    async def _send_reminder(self, reminder: Reminder):
        """Send a single reminder notification"""
        try:
            if reminder.is_dm:
                user = await self.client.fetch_user(reminder.user_id)
                await user.send(f"⏰ **Reminder:** {reminder.message}")
            else:
                channel = self.client.get_channel(reminder.channel_id)
                if channel:
                    await channel.send(f"<@{reminder.user_id}> ⏰ **Reminder:** {reminder.message}")
        except Exception as e:
            home_log.log_sync(f"Error sending reminder: {e}")

    async def _deliver_due(self) -> None:
        now = datetime.now()
        due = [r for r in self.reminders.values() if r.trigger_time <= now]
        if not due:
            return
        for r in due:
            self.reminders.pop(r.id, None)
        self.save()
        for r in due:
            await self._send_reminder(r)

    def _seconds_until_next(self) -> float:
        if not self.reminders:
            return _MAX_SLEEP_SECONDS
        nxt = min(r.trigger_time for r in self.reminders.values())
        return max(0.5, min(_MAX_SLEEP_SECONDS, (nxt - datetime.now()).total_seconds()))

    async def _run(self) -> None:
        while True:
            try:
                await self._deliver_due()
            except Exception as e:
                home_log.log_sync(f"⚠️ Reminder loop error: {e}")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._seconds_until_next())
            except asyncio.TimeoutError:
                pass

    def start(self, client) -> None:
        """Start delivering reminders on the running event loop (call from setup_hook)."""
        self.client = client
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="reminders")
            home_log.log_sync("✅ Reminder service started")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self.save()


# Global instance
reminder_manager = ReminderManager()
