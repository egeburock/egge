import logging

from src.models import Signal

log = logging.getLogger(__name__)


def format_signal(s: Signal) -> str:
    emoji = "🟢" if s.direction == "LONG" else "🔴"
    strength = "GÜÇLÜ " if s.strong else ""
    lines = [f"{emoji} {strength}{s.direction} — {s.symbol}",
             f"Fiyat: {s.price:.6g} | Zaman dilimi: {s.timeframe}",
             "Tetikleyen kurallar:"]
    lines += [f"• {h.detail}" for h in s.hits]
    lines.append(f"Skor: {s.score}")
    if s.stop:
        lines.append(f"Stop önerisi: {s.stop:.6g} (ATR bazlı)")
    if s.target:
        lines.append(f"Hedef: {s.target:.6g}")
    return "\n".join(lines)


class Notifier:
    def __init__(self, db, token: str, chat_id: str, dry_run: bool):
        self.db = db
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self._bot = None

    async def send(self, s: Signal):
        text = format_signal(s)
        log.info("Sinyal: %s", text.replace("\n", " | "))
        self.db.enqueue_message(text)
        if self.dry_run or not self.token:
            return
        await self._flush()

    async def _flush(self):
        from telegram import Bot
        if self._bot is None:
            self._bot = Bot(self.token)
        while (m := self.db.next_pending_message()):
            try:
                await self._bot.send_message(chat_id=self.chat_id, text=m["text"])
                self.db.mark_message_sent(m["id"])
            except Exception as e:
                log.warning("Telegram gönderilemedi: %s", e)
                break
